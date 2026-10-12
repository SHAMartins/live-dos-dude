"""Teste de fumaça do SFU: publica vídeo sintético e mede a latência ponta a ponta.

Valida, em ordem, cada camada que o cliente desktop vai usar:
  1. sinalização (wss/TLS + token)    -> falha: DNS, certificado, Caddy, chaves
  2. negociação  (assinatura da track) -> falha: SFU, permissões do token
  3. mídia       (frames chegando)     -> falha: UDP 50000-60000 / TCP 7881 bloqueados

Dois participantes no mesmo processo: o publicador marca cada frame com o
instante da captura (FrameMetadata.user_timestamp) e o assinante, que recebe
o vídeo de volta pelo SFU, calcula captura -> frame decodificado. Como os dois
usam o mesmo relógio, não há nada para sincronizar.

O encode é forçado para software (ENCODER_BACKEND_SOFTWARE), exatamente como o
cliente desktop fará, e o relatório confirma qual encoder foi usado.

Uso:
    python smoke_test.py                       # LiveKit local (infra_sfu/dev)
    python smoke_test.py --url wss://live.seudominio.com --api-key ... --api-secret ...
    python smoke_test.py --resolucao 1080p --fps 60 --bitrate 6000
    python smoke_test.py --manter              # fica no ar para assistir no navegador
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time

import numpy as np
from livekit import rtc

from comum import adicionar_args_conexao, criar_token

RESOLUCOES = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}
CODECS = {"vp8": rtc.VideoCodec.VP8, "h264": rtc.VideoCodec.H264}
AQUECIMENTO_S = 2.0  # descarta o início (keyframe inicial, rampa de bitrate)
TIMEOUT_ETAPA_S = 10.0

DICAS = {
    "sinalizacao": [
        "a URL usa wss:// em produção e ws:// só no local?",
        "o DNS do domínio aponta para o IP público da VM?",
        "TCP 443 (e 80, para o certificado) está aberto na Security List E no iptables?",
        "'docker compose logs caddy' mostra o certificado emitido?",
        "a API key/secret são as mesmas do .env da VM?",
    ],
    "negociacao": [
        "'docker compose logs livekit' mostra erro ao publicar?",
        "o token do publicador tem can_publish e o do assinante can_subscribe?",
    ],
    "midia": [
        "UDP 50000-60000 está aberto na Security List E no iptables da VM?",
        "TCP 7881 está aberto (fallback quando UDP é bloqueado)?",
        "os logs do livekit mostram 'could not validate external IP'? Então o UDP não está chegando.",
    ],
}


class PadraoDeTeste:
    """Gradiente rolando na horizontal: movimento contínuo para o encoder trabalhar."""

    def __init__(self, largura: int, altura: int) -> None:
        self.largura, self.altura = largura, altura
        x = np.linspace(0, 255, largura, dtype=np.float32)[None, :]
        y = np.linspace(0, 255, altura, dtype=np.float32)[:, None]
        self.base = np.empty((altura, largura, 4), dtype=np.uint8)
        self.base[..., 0] = x
        self.base[..., 1] = y
        self.base[..., 2] = (x + y) / 2
        self.base[..., 3] = 255

    def frame(self, n: int) -> rtc.VideoFrame:
        img = np.roll(self.base, (n * 8) % self.largura, axis=1)
        return rtc.VideoFrame(self.largura, self.altura, rtc.VideoBufferType.RGBA, img.tobytes())


async def publicar_frames(fonte: rtc.VideoSource, padrao: PadraoDeTeste, fps: int, parar: asyncio.Event) -> None:
    intervalo = 1 / fps
    proximo = time.perf_counter()
    n = 0
    while not parar.is_set():
        frame = padrao.frame(n)
        fonte.capture_frame(frame, metadata=rtc.FrameMetadata(user_timestamp=time.time_ns() // 1000, frame_id=n))
        n += 1
        proximo += intervalo
        espera = proximo - time.perf_counter()
        if espera > 0:
            await asyncio.sleep(espera)
        else:
            proximo = time.perf_counter()  # atrasou: não acumula dívida de frames
            await asyncio.sleep(0)


class Medidor:
    def __init__(self, inicio_medicao: float) -> None:
        self.inicio_medicao = inicio_medicao
        self.primeiro_frame = asyncio.Event()
        self.frames_medidos = 0
        self.sem_metadata = 0
        self.latencias_ms: list[float] = []
        self.resolucao = (0, 0)

    async def consumir(self, stream: rtc.VideoStream) -> None:
        async for evento in stream:
            agora_us = time.time_ns() // 1000
            self.primeiro_frame.set()
            self.resolucao = (evento.frame.width, evento.frame.height)
            if time.perf_counter() < self.inicio_medicao:
                continue
            self.frames_medidos += 1
            if evento.metadata is None or not evento.metadata.user_timestamp:
                self.sem_metadata += 1
                continue
            self.latencias_ms.append((agora_us - evento.metadata.user_timestamp) / 1000)


def descrever_transporte(stats: list) -> str:
    candidatos, pares = {}, []
    for st in stats:
        tipo = st.WhichOneof("stats")
        if tipo in ("local_candidate", "remote_candidate"):
            c = getattr(st, tipo)
            candidatos[c.rtc.id] = c.candidate
        elif tipo == "candidate_pair":
            pares.append(st.candidate_pair.candidate_pair)
    ativo = next((p for p in pares if p.nominated), None)
    if ativo is None:
        return "desconhecido"
    local = candidatos.get(ativo.local_candidate_id)
    remoto = candidatos.get(ativo.remote_candidate_id)
    if local is None or remoto is None:
        return "desconhecido"
    # Candidatos peer-reflexive têm o endereço ocultado pela libwebrtc (privacidade).
    endereco = f"{remoto.address}:{remoto.port}" if remoto.address else "endereço oculto"
    tipo_remoto = rtc_enum_nome(remoto, "candidate_type").lower()
    texto = (f"{local.protocol.upper()}, SFU {endereco} ({tipo_remoto}), "
             f"RTT {ativo.current_round_trip_time * 1000:.0f} ms")
    if local.protocol.lower() == "tcp":
        texto += "  (!) caiu no fallback TCP: o UDP está bloqueado em algum ponto"
    return texto


def descrever_encoder(stats: list) -> list[str]:
    for st in stats:
        if st.WhichOneof("stats") != "outbound_rtp":
            continue
        o = st.outbound_rtp.outbound
        if not o.frames_encoded:
            continue
        tipo = "HARDWARE (!)" if o.power_efficient_encoder else "software (CPU)"
        ms_por_frame = o.total_encode_time / o.frames_encoded * 1000
        linhas = [
            f"encoder: {o.encoder_implementation or '?'} -> {tipo}",
            f"encode médio: {ms_por_frame:.1f} ms/frame | enviado {o.frame_width}x{o.frame_height} "
            f"@ {o.frames_per_second:.0f} fps | alvo {o.target_bitrate / 1000:.0f} kbps",
        ]
        if o.quality_limitation_reason:
            linhas.append(f"qualidade limitada por: {rtc_enum_nome(o, 'quality_limitation_reason')}")
        return linhas
    return ["encoder: sem estatísticas de saída"]


def rtc_enum_nome(msg, campo: str) -> str:
    valor = getattr(msg, campo)
    enum = msg.DESCRIPTOR.fields_by_name[campo].enum_type
    return enum.values_by_number[valor].name if enum else str(valor)


def etapa(texto: str) -> None:
    print(f"\n==> {texto}", flush=True)


def falha(motivo: str, chave_dicas: str, erro: BaseException | None = None) -> int:
    print(f"  FALHOU: {motivo}" + (f" ({type(erro).__name__}: {erro})" if erro else ""))
    print("  Verifique:")
    for dica in DICAS[chave_dicas]:
        print(f"   - {dica}")
    return 1


async def executar(args: argparse.Namespace) -> int:
    largura, altura = RESOLUCOES[args.resolucao]
    pub, sub = rtc.Room(), rtc.Room()
    parar = asyncio.Event()
    tarefas: list[asyncio.Task] = []
    try:
        etapa(f"1/3 Sinalização: {args.url}")
        try:
            await pub.connect(args.url, criar_token(args, "smoke-publicador", publicar=True),
                              rtc.RoomOptions(auto_subscribe=False))
        except Exception as e:
            return falha("não conectou ao SFU", "sinalizacao", e)
        print(f"  ok: conectado à sala '{args.sala}'")

        fonte = rtc.VideoSource(largura, altura, is_screencast=True)
        track = rtc.LocalVideoTrack.create_video_track("tela", fonte)
        opcoes = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_SCREENSHARE,
            video_codec=CODECS[args.codec],
            video_encoder="ENCODER_BACKEND_SOFTWARE",
            simulcast=False,
            video_encoding=rtc.VideoEncoding(max_framerate=args.fps, max_bitrate=args.bitrate * 1000),
            frame_metadata_features=[rtc.FrameMetadataFeature.FMF_USER_TIMESTAMP,
                                     rtc.FrameMetadataFeature.FMF_FRAME_ID],
        )
        tarefas.append(asyncio.create_task(publicar_frames(fonte, PadraoDeTeste(largura, altura), args.fps, parar)))
        await pub.local_participant.publish_track(track, opcoes)
        print(f"  ok: publicando {largura}x{altura} @ {args.fps} fps, {args.codec.upper()} {args.bitrate} kbps")

        if args.manter:
            token_espectador = criar_token(args, "espectador-navegador", publicar=False)
            print("\nAssista em https://meet.livekit.io -> aba 'Custom':")
            print(f"  LiveKit URL: {args.url}")
            print(f"  Token:       {token_espectador}")
            print("\nCtrl+C para encerrar.")
            await asyncio.Event().wait()

        etapa("2/3 Negociação: assinando a track pelo SFU")
        recebida: asyncio.Future[rtc.Track] = asyncio.get_running_loop().create_future()

        @sub.on("track_subscribed")
        def _ao_assinar(track_remota: rtc.Track, _pub, _participante) -> None:
            if track_remota.kind == rtc.TrackKind.KIND_VIDEO and not recebida.done():
                recebida.set_result(track_remota)

        try:
            await sub.connect(args.url, criar_token(args, "smoke-assinante", publicar=False))
            track_remota = await asyncio.wait_for(recebida, TIMEOUT_ETAPA_S)
        except Exception as e:
            return falha("o assinante não recebeu a track", "negociacao", e)
        print("  ok: track assinada")

        etapa("3/3 Mídia: aguardando frames")
        medidor = Medidor(inicio_medicao=time.perf_counter() + AQUECIMENTO_S)
        stream = rtc.VideoStream(track_remota)
        tarefas.append(asyncio.create_task(medidor.consumir(stream)))
        try:
            await asyncio.wait_for(medidor.primeiro_frame.wait(), TIMEOUT_ETAPA_S)
        except TimeoutError:
            return falha(f"nenhum frame em {TIMEOUT_ETAPA_S:.0f} s", "midia")
        print(f"  ok: frames chegando; medindo por {args.duracao} s...")

        inicio = time.perf_counter()
        await asyncio.sleep(AQUECIMENTO_S + args.duracao)
        janela = time.perf_counter() - max(inicio, medidor.inicio_medicao)

        stats_pub = await track.get_stats()
        stats_sub = await track_remota.get_stats()
        await stream.aclose()

        etapa("Resultado")
        fps_recebido = medidor.frames_medidos / janela if janela > 0 else 0
        w, h = medidor.resolucao
        print(f"  recebido: {w}x{h} @ {fps_recebido:.1f} fps (alvo {args.fps})")
        for linha in descrever_encoder(stats_pub):
            print(f"  {linha}")
        print(f"  transporte publicador: {descrever_transporte(stats_pub)}")
        print(f"  transporte assinante:  {descrever_transporte(stats_sub)}")

        lat = medidor.latencias_ms
        if len(lat) >= 2:
            p95 = statistics.quantiles(lat, n=20)[18]
            print(f"  latência captura->decodificado: mediana {statistics.median(lat):.0f} ms | "
                  f"p95 {p95:.0f} ms | mín {min(lat):.0f} | máx {max(lat):.0f}  ({len(lat)} amostras)")
        else:
            print(f"  latência: indisponível (frames sem metadata: {medidor.sem_metadata})")

        if fps_recebido < args.fps * 0.5:
            print(f"\nRESULTADO: FALHOU (fps recebido muito abaixo do alvo)")
            return 1
        print("\nRESULTADO: OK")
        return 0
    finally:
        parar.set()
        for t in tarefas:
            t.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        await sub.disconnect()
        await pub.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    adicionar_args_conexao(parser)
    parser.add_argument("--resolucao", choices=RESOLUCOES, default="720p")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--bitrate", type=int, default=2500, help="bitrate máximo em kbps (padrão: 2500)")
    parser.add_argument("--codec", choices=CODECS, default="vp8")
    parser.add_argument("--duracao", type=int, default=10, help="segundos de medição (padrão: 10)")
    parser.add_argument("--manter", action="store_true", help="só publica, sem medir, até Ctrl+C")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(executar(args)))
    except KeyboardInterrupt:
        print("\nencerrado.")


if __name__ == "__main__":
    main()
