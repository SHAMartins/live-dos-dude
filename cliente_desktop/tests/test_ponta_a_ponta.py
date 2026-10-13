"""Tudo de verdade: backend + cliente + captura da tela + LiveKit local, e um espectador assistindo.

Precisa do LiveKit de dev (infra_sfu/dev) e de uma sessão de desktop do Windows.
"""

from __future__ import annotations

import asyncio
import socket
import sys

import pytest
from livekit import api, rtc

from app.services.transmissao import PedidoTransmissao
from auxiliares import FULANO, parear
from cliente.estado import Estado
from cliente.media.captura import ThreadMidia
from cliente.media.cursor import ativar_dpi_awareness
from cliente.media.escala import tamanho_de_saida
from cliente.media.publicador import Publicador

pytestmark = [pytest.mark.livekit, pytest.mark.tela]


def _livekit_no_ar() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 7880), timeout=0.5):
            return True
    except OSError:
        return False


if sys.platform != "win32" or not _livekit_no_ar():
    pytest.skip("precisa do Windows e do LiveKit local em localhost:7880", allow_module_level=True)


@pytest.fixture
def midia():
    ativar_dpi_awareness()
    m = ThreadMidia()
    yield m
    m.fechar()


async def test_transmitir_pelo_backend_e_um_espectador_recebe_a_tela(backend, criar_cliente, midia):
    publicador = Publicador(midia)
    cliente = criar_cliente(publicador=publicador)
    await parear(backend, cliente)
    largura_tela, altura_tela, _ = await asyncio.wrap_future(midia.preparar())
    esperado = tamanho_de_saida(largura_tela, altura_tela, 1280, 720)

    sessao = await backend.servico.transmitir(FULANO, PedidoTransmissao("e2e", "720p", 30, 2500, "vp8"))
    snapshot = await cliente.esperar(Estado.TRANSMITINDO)
    assert snapshot.resolucao == esperado and snapshot.captura == "dxgi"
    assert snapshot.audio, "o som do PC deveria ir junto (config padrão)"

    # Espectador entra pelo link com chave (o mesmo que o bot posta) e conta os quadros que chegam.
    acesso = await backend.servico.acesso_por_chave(sessao.chave_espectador)
    url, token = acesso.livekit_url, acesso.token
    espectador = rtc.Room()
    laco = asyncio.get_running_loop()
    recebidas: dict[int, asyncio.Future[tuple[rtc.Track, rtc.RemoteTrackPublication]]] = {
        rtc.TrackKind.KIND_VIDEO: laco.create_future(), rtc.TrackKind.KIND_AUDIO: laco.create_future()}

    def inscreveu(track: rtc.Track, publicacao: rtc.RemoteTrackPublication, *_) -> None:
        futuro = recebidas.get(track.kind)
        if futuro is not None and not futuro.done():
            futuro.set_result((track, publicacao))

    espectador.on("track_subscribed", inscreveu)
    await espectador.connect(url, token)
    try:
        track, _ = await asyncio.wait_for(recebidas[rtc.TrackKind.KIND_VIDEO], 10)
        quadros, dimensoes = 0, set()
        stream = rtc.VideoStream(track)
        async with asyncio.timeout(10):
            async for evento in stream:
                quadros += 1
                dimensoes.add((evento.frame.width, evento.frame.height))
                if quadros >= 30:
                    break
        await stream.aclose()
        assert esperado in dimensoes

        # A restrição do projeto, verificada no encoder de verdade.
        saida = next(s.outbound_rtp.outbound for s in await publicador.track.get_stats()
                     if s.WhichOneof("stats") == "outbound_rtp")
        assert saida.encoder_implementation == "libvpx"
        assert not saida.power_efficient_encoder

        # Telemetria com o encoder de verdade: a primeira amostra só marca o ponto de partida dos deltas.
        await publicador.telemetria()
        await asyncio.sleep(1.5)
        amostra = await publicador.telemetria()
        assert amostra["codificador"] == "libvpx" and amostra["captura"] == "dxgi"
        assert amostra["enviado_fps"] > 0 and amostra["encode_ms"] >= 0
        assert amostra["limite"] in {"nenhum", "cpu", "banda", "outro"}
        assert amostra["largura"] == esperado[0] and amostra["altura"] == esperado[1]
        assert 0 <= amostra["cpu_app_pct"] <= 100 and 0 <= amostra["cpu_sistema_pct"] <= 100

        # O som chega como áudio de compartilhamento de tela, estéreo a 48 kHz.
        som, publicacao = await asyncio.wait_for(recebidas[rtc.TrackKind.KIND_AUDIO], 10)
        assert publicacao.source == rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO
        fluxo, blocos = rtc.AudioStream(som, sample_rate=48000, num_channels=2), 0
        async with asyncio.timeout(10):
            async for evento in fluxo:
                assert evento.frame.sample_rate == 48000 and evento.frame.num_channels == 2
                blocos += 1
                if blocos >= 50:
                    break
        await fluxo.aclose()
    finally:
        await espectador.disconnect()

    assert (await backend.servico.parar(FULANO)).confirmado
    await cliente.esperar(Estado.ONLINE)
    async with api.LiveKitAPI("http://localhost:7880", backend.config.livekit_api_key,
                              backend.config.livekit_api_secret.get_secret_value()) as lk:
        async with asyncio.timeout(5):
            while True:
                resposta = await lk.room.list_participants(api.ListParticipantsRequest(room="e2e"))
                if f"pc-{FULANO.discord_id}" not in {p.identity for p in resposta.participants}:
                    break
                await asyncio.sleep(0.2)
    assert (await backend.repo.sessao(sessao.id)).estado == "encerrada"


async def test_aplicar_agora_troca_tela_por_janela_e_o_espectador_continua(backend, criar_cliente, midia):
    from cliente.media import fontes

    janelas = [j for j in fontes.listar_janelas() if not fontes.janela_minimizada(j.hwnd) and j.largura >= 400]
    if not janelas:
        pytest.skip("nenhuma janela visível para capturar")
    janela = max(janelas, key=lambda j: j.largura * j.altura)

    publicador = Publicador(midia)
    cliente = criar_cliente(publicador=publicador, resolucao="720p", fps=30, bitrate_kbps=2000)
    await parear(backend, cliente)
    sessao = await backend.servico.transmitir(FULANO, PedidoTransmissao("e2e-troca"))
    await cliente.esperar(Estado.TRANSMITINDO)

    acesso = await backend.servico.acesso_por_chave(sessao.chave_espectador)
    espectador = rtc.Room()
    tracks: asyncio.Queue[rtc.Track] = asyncio.Queue()
    sons: asyncio.Queue[rtc.Track] = asyncio.Queue()
    espectador.on("track_subscribed", lambda track, *_: (
        tracks if track.kind == rtc.TrackKind.KIND_VIDEO else sons).put_nowait(track))
    await espectador.connect(acesso.livekit_url, acesso.token)

    async def contar_quadros(track: rtc.Track, quantos: int) -> set[tuple[int, int]]:
        dimensoes, stream = set(), rtc.VideoStream(track)
        async with asyncio.timeout(10):
            async for evento in stream:
                dimensoes.add((evento.frame.width, evento.frame.height))
                quantos -= 1
                if quantos <= 0:
                    break
        await stream.aclose()
        return dimensoes

    try:
        assert await contar_quadros(await asyncio.wait_for(tracks.get(), 10), 15)
        # Troca para a janela com a transmissão no ar: mesma sessão, mesmo link.
        cliente.controlador.config.fonte = janela.como_fonte()
        await cliente.controlador.aplicar_configuracao()
        assert cliente.snapshots[-1].captura == "janela"
        assert await contar_quadros(await asyncio.wait_for(tracks.get(), 10), 15)
        await asyncio.wait_for(sons.get(), 10)  # o som é republicado junto
        await asyncio.wait_for(sons.get(), 10)
        assert cliente.controlador.sessao_id == sessao.id
        assert (await backend.servico.acesso_por_chave(sessao.chave_espectador)) is not None  # link ainda vale
    finally:
        await espectador.disconnect()
    assert (await backend.servico.parar(FULANO)).confirmado


async def test_h264_pedido_sai_em_vp8_porque_o_openh264_desaba_com_imagem_variada(backend, midia):
    from cliente.controlador import ParametrosMidia

    publicador = Publicador(midia)
    token = backend.servico.tokens.publicador(FULANO.discord_id, FULANO.nome, "h264-forcado")
    info = await publicador.iniciar(backend.config.livekit_url, token, "h264-forcado",
                                    ParametrosMidia(1280, 720, 30, 2000, "h264", False), None, lambda motivo: None)
    try:
        assert info.codec == "vp8"
        await asyncio.sleep(2)  # as estatísticas do encoder só aparecem depois dos primeiros quadros
        saida = next(s.outbound_rtp.outbound for s in await publicador.track.get_stats()
                     if s.WhichOneof("stats") == "outbound_rtp")
        assert saida.encoder_implementation == "libvpx"
    finally:
        await publicador.parar()
