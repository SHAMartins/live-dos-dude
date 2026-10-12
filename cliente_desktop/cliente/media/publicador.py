from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from livekit import rtc  # carregado na inicialização do app: o import da lib nativa trava o loop

from cliente.controlador import ParametrosMidia
from cliente.media import fontes
from cliente.media.captura import ThreadMidia
from cliente.media.escala import tamanho_de_saida

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


class Publicador:
    """Uma transmissão por vez. A sala do LiveKit fica conectada durante a sessão inteira; trocar
    fonte ou qualidade só republica a track (os espectadores continuam no mesmo link)."""

    def __init__(self, midia: ThreadMidia, sacrificar: str = "fps") -> None:
        self.midia = midia
        self._degradacao = DEGRADACAO[sacrificar]
        self._sala: rtc.Room | None = None
        self._nome_sala = ""
        self._track: rtc.LocalVideoTrack | None = None
        self._ao_cair: Callable[[str], None] = lambda _motivo: None
        self._parando = False

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
        await asyncio.to_thread(self.midia.parar)
        if self._track is not None:
            await self._sala.local_participant.unpublish_track(self._track.sid)
            self._track = None
        return await self._publicar(self._sala, parametros, fonte)

    async def parar(self) -> None:
        self._parando = True
        await asyncio.to_thread(self.midia.parar)
        if self._sala is not None:
            await self._sala.disconnect()
        self._sala, self._track = None, None

    async def _publicar(self, sala: rtc.Room, parametros: ParametrosMidia, fonte: dict | None) -> InfoPublicacao:
        loop = asyncio.get_running_loop()
        largura_fonte, altura_fonte, nome_captura = await asyncio.wrap_future(self.midia.preparar(fonte))
        largura, altura = tamanho_de_saida(largura_fonte, altura_fonte, parametros.alvo_largura, parametros.alvo_altura)

        origem = rtc.VideoSource(largura, altura, is_screencast=True)
        track = rtc.LocalVideoTrack.create_video_track("tela", origem)
        opcoes = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_SCREENSHARE,
            video_codec=rtc.VideoCodec.H264 if parametros.codec == "h264" else rtc.VideoCodec.VP8,
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
        descricao = fontes.descrever(fonte)
        log.info("publicando %dx%d@%d %d kbps (%s, captura %s: %s) na sala %s", largura, altura,
                 parametros.fps, parametros.bitrate_kbps, parametros.codec, nome_captura, descricao, self._nome_sala)
        return InfoPublicacao(self._nome_sala, largura, altura, parametros.fps, parametros.bitrate_kbps,
                              parametros.codec, nome_captura, descricao)


def rtc_nome_motivo(motivo) -> str:
    try:
        return rtc.DisconnectReason.Name(motivo)
    except (AttributeError, ValueError, TypeError):
        return str(motivo)
