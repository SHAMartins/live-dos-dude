from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from livekit import rtc  # carregado na inicialização do app: o import da lib nativa trava o loop

from cliente.controlador import ParametrosMidia
from cliente.media import fontes
from cliente.media.audio import TAXA, CANAIS, CapturaAudio
from cliente.media.captura import ThreadMidia
from cliente.media.escala import tamanho_de_saida
from cliente.media.medidas import CpuDoSistema, UsoDaGpu

log = logging.getLogger(__name__)

DEGRADACAO = {
    "fps": "DEGRADATION_PREFERENCE_MAINTAIN_RESOLUTION",   # texto nítido, fps cai
    "resolucao": "DEGRADATION_PREFERENCE_MAINTAIN_FRAMERATE",  # movimento fluido, resolução cai
}


@dataclass(frozen=True)
class InfoPublicacao:
    sala: str
    largura: int
    altura: int
    fps: int
    bitrate_kbps: int
    codec: str
    captura: str   # "dxgi", "gdi" ou "janela"
    fonte: str     # descrição para a UI
    audio: bool    # o som do PC está sendo publicado


class Publicador:
    """Uma transmissão por vez. A sala do LiveKit fica conectada durante a sessão inteira; trocar
    fonte ou qualidade só republica a track (os espectadores continuam no mesmo link)."""

    def __init__(self, midia: ThreadMidia, sacrificar: str = "fps", audio: CapturaAudio | None = None) -> None:
        self.midia = midia
        self.audio = audio or CapturaAudio()
        self._track_audio: rtc.LocalAudioTrack | None = None
        self._degradacao = DEGRADACAO[sacrificar]
        self._sala: rtc.Room | None = None
        self._nome_sala = ""
        self._track: rtc.LocalVideoTrack | None = None
        self._ao_cair: Callable[[str], None] = lambda _motivo: None
        self._parando = False
        self._cpu_sistema, self._gpu = CpuDoSistema(), UsoDaGpu()
        self._amostra_anterior: tuple[float, float] | None = None   # (relógio, CPU do processo)
        self._stats_anterior: tuple[float, int, float] | None = None  # (relógio, quadros, tempo de encode)
        self._nome_captura, self._fps_alvo = "", 0

    @property
    def track(self) -> rtc.LocalVideoTrack | None:
        return self._track

    async def iniciar(self, livekit_url: str, token: str, nome_sala: str, parametros: ParametrosMidia,
                      fonte: dict | None, ao_cair: Callable[[str], None]) -> InfoPublicacao:
        self._ao_cair, self._parando, self._nome_sala = ao_cair, False, nome_sala
        sala = rtc.Room()

        @sala.on("disconnected")
        def _desconectou(motivo) -> None:
            if not self._parando:
                self._ao_cair(f"conexão com o LiveKit caiu ({rtc_nome_motivo(motivo)})")

        await sala.connect(livekit_url, token, rtc.RoomOptions(auto_subscribe=False))
        try:
            info = await self._publicar(sala, parametros, fonte)
        except BaseException:
            self._parando = True
            await sala.disconnect()
            raise
        self._sala = sala
        return info

    async def trocar(self, parametros: ParametrosMidia, fonte: dict | None) -> InfoPublicacao:
        """Aplica nova fonte/qualidade sem sair da sala: ~1-2 s de vídeo parado para quem assiste."""
        if self._sala is None:
            raise RuntimeError("não há transmissão para alterar")
        await self._parar_capturas()
        for track in (self._track, self._track_audio):
            if track is not None:
                await self._sala.local_participant.unpublish_track(track.sid)
        self._track = self._track_audio = None
        return await self._publicar(self._sala, parametros, fonte)

    async def parar(self) -> None:
        self._parando = True
        await self._parar_capturas()
        if self._sala is not None:
            await self._sala.disconnect()
        self._sala, self._track, self._track_audio = None, None, None

    async def telemetria(self) -> dict | None:
        """Amostra de desempenho desde a chamada anterior (None se não há vídeo no ar)."""
        track = self._track
        if track is None:
            return None
        agora, cpu = time.perf_counter(), time.process_time()
        nucleos = os.cpu_count() or 1
        dados: dict = {
            "captura": self._nome_captura, "alvo_fps": self._fps_alvo, "nucleos": nucleos,
            "captura_fps": round(self.midia.fps_real, 1), "captura_ms": round(self.midia.ms_por_frame, 1),
        }
        if self._amostra_anterior is not None:
            antes_t, antes_c = self._amostra_anterior
            if agora > antes_t:
                dados["cpu_app_pct"] = round((cpu - antes_c) / (agora - antes_t) / nucleos * 100, 1)
        self._amostra_anterior = (agora, cpu)
        for campo, valor in (("cpu_sistema_pct", self._cpu_sistema.percentual()), ("gpu_pct", self._gpu.percentual())):
            if valor is not None:
                dados[campo] = round(valor, 1)
        try:
            for item in await asyncio.wait_for(track.get_stats(), 2):
                if item.WhichOneof("stats") != "outbound_rtp":
                    continue
                s = item.outbound_rtp.outbound
                if self._stats_anterior is not None and agora > self._stats_anterior[0]:
                    antes_t, antes_q, antes_e = self._stats_anterior
                    quadros = s.frames_encoded - antes_q
                    dados["enviado_fps"] = round(quadros / (agora - antes_t), 1)
                    if quadros > 0:
                        dados["encode_ms"] = round((s.total_encode_time - antes_e) / quadros * 1000, 1)
                self._stats_anterior = (agora, s.frames_encoded, s.total_encode_time)
                dados["codificador"] = s.encoder_implementation or None
                dados["largura"], dados["altura"] = s.frame_width or None, s.frame_height or None
                dados["limite"] = _LIMITES.get(int(s.quality_limitation_reason), "outro")
                break
        except Exception:  # noqa: BLE001 - telemetria nunca derruba a transmissão
            log.debug("sem estatísticas do encoder nesta amostra", exc_info=True)
        return dados

    async def _parar_capturas(self) -> None:
        await asyncio.gather(asyncio.to_thread(self.midia.parar), asyncio.to_thread(self.audio.parar))

    async def _publicar_audio(self, sala: rtc.Room) -> bool:
        """Publica o som do PC. Se falhar (sem dispositivo de saída...), a transmissão segue só com vídeo."""
        origem = rtc.AudioSource(TAXA, CANAIS)
        track = rtc.LocalAudioTrack.create_audio_track("som", origem)
        opcoes = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO,
            dtx=False,                             # música/jogo: não cortar os trechos baixos
            audio_encoding={"max_bitrate": 128_000},  # Opus estéreo com qualidade de música
        )
        try:
            await asyncio.to_thread(self.audio.iniciar, origem, asyncio.get_running_loop())
            await sala.local_participant.publish_track(track, opcoes)
        except Exception as erro:  # noqa: BLE001 - som é opcional: não derruba o vídeo
            log.warning("transmitindo sem som: %s", erro)
            await asyncio.to_thread(self.audio.parar)
            return False
        self._track_audio = track
        return True

    async def _publicar(self, sala: rtc.Room, parametros: ParametrosMidia, fonte: dict | None) -> InfoPublicacao:
        loop = asyncio.get_running_loop()
        largura_fonte, altura_fonte, nome_captura = await asyncio.wrap_future(self.midia.preparar(fonte))
        largura, altura = tamanho_de_saida(largura_fonte, altura_fonte, parametros.alvo_largura, parametros.alvo_altura)

        # Só VP8: o H.264 por software (OpenH264) entrega 3-7 fps com imagem variada em 720p/30 (VP8: 30 fps).
        # Vale também quando um backend antigo ainda pede h264.
        if parametros.codec != "vp8":
            log.warning("codec %s pedido; usando vp8 (o H.264 por software não aguenta imagem variada)", parametros.codec)
            parametros = replace(parametros, codec="vp8")
        origem = rtc.VideoSource(largura, altura, is_screencast=True)
        track = rtc.LocalVideoTrack.create_video_track("tela", origem)
        opcoes = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_SCREENSHARE,
            video_codec=rtc.VideoCodec.VP8,
            video_encoder="ENCODER_BACKEND_SOFTWARE",  # restrição do projeto: nada de NVENC/AMF
            simulcast=False,                            # simulcast triplicaria o custo de CPU
            video_encoding=rtc.VideoEncoding(max_framerate=parametros.fps, max_bitrate=parametros.bitrate_kbps * 1000),
            degradation_preference=self._degradacao,
        )
        # A captura começa antes do publish para o encoder já ter frames quando a negociação terminar.
        self.midia.iniciar(origem, largura, altura, parametros.fps,
                           ao_falhar=lambda motivo: loop.call_soon_threadsafe(self._ao_cair, motivo))
        try:
            await sala.local_participant.publish_track(track, opcoes)
        except BaseException:
            await asyncio.to_thread(self.midia.parar)
            raise
        self._track = track
        self._nome_captura, self._fps_alvo = nome_captura, parametros.fps
        self._stats_anterior = None
        com_audio = await self._publicar_audio(sala) if parametros.audio else False
        descricao = fontes.descrever(fonte)
        log.info("publicando %dx%d@%d %d kbps (%s, captura %s: %s, %s) na sala %s", largura, altura,
                 parametros.fps, parametros.bitrate_kbps, parametros.codec, nome_captura, descricao,
                 "com som" if com_audio else "sem som", self._nome_sala)
        return InfoPublicacao(self._nome_sala, largura, altura, parametros.fps, parametros.bitrate_kbps,
                              parametros.codec, nome_captura, descricao, com_audio)


# QualityLimitationReason do WebRTC (NONE, CPU, BANDWIDTH, OTHER) em nomes curtos para o log.
_LIMITES = {0: "nenhum", 1: "cpu", 2: "banda", 3: "outro"}


def rtc_nome_motivo(motivo) -> str:
    try:
        return rtc.DisconnectReason.Name(motivo)
    except (AttributeError, ValueError, TypeError):
        return str(motivo)
