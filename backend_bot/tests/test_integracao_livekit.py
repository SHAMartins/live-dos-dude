"""Ponta a ponta com o LiveKit local: o token emitido pelo backend publica de verdade no SFU.

Precisa do LiveKit de dev rodando (infra_sfu/dev). Sem ele, os testes são pulados.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from livekit import api

from app.services.transmissao import PedidoTransmissao
from auxiliares import FULANO

pytestmark = pytest.mark.livekit


def _livekit_no_ar() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 7880), timeout=0.5):
            return True
    except OSError:
        return False


if not _livekit_no_ar():
    pytest.skip("LiveKit local não está rodando em localhost:7880", allow_module_level=True)


async def _participantes(ambiente, sala: str) -> dict[str, list]:
    async with api.LiveKitAPI("http://localhost:7880", ambiente.config.livekit_api_key,
                              ambiente.config.livekit_api_secret.get_secret_value()) as lk:
        resposta = await lk.room.list_participants(api.ListParticipantsRequest(room=sala))
        return {p.identity: list(p.tracks) for p in resposta.participants}


async def test_transmitir_publica_tela_no_livekit_e_parar_remove(ambiente, cliente_pareado):
    await cliente_pareado(publicar=True)
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("integracao", "480p", 15, 800))

    # O SFU só lista a track quando os primeiros pacotes RTP chegam, um instante depois do publish.
    async with asyncio.timeout(5):
        while not (tracks := (await _participantes(ambiente, sessao.sala)).get(f"pc-{FULANO.discord_id}")):
            await asyncio.sleep(0.1)
    assert [t.source for t in tracks] == [api.TrackSource.SCREEN_SHARE]
    assert (tracks[0].width, tracks[0].height) == (854, 480)

    await ambiente.servico.parar(FULANO)
    async with asyncio.timeout(5):
        while f"pc-{FULANO.discord_id}" in await _participantes(ambiente, sessao.sala):
            await asyncio.sleep(0.2)


async def test_token_de_publicador_nao_publica_camera(ambiente, cliente_pareado):
    """O token só libera screen_share: mesmo um cliente adulterado não consegue abrir a câmera.

    O LiveKit recusa em silêncio (loga 'no permission to publish track' e não responde ao
    cliente), então a prova é do lado do servidor: o participante entra, mas sem track.
    """
    from livekit import rtc

    cliente = await cliente_pareado()
    await ambiente.servico.transmitir(FULANO, PedidoTransmissao("integracao-camera"))
    iniciar = await cliente.esperar("iniciar")

    sala = rtc.Room()
    await sala.connect(iniciar["livekit_url"], iniciar["token"])
    track = rtc.LocalVideoTrack.create_video_track("camera", rtc.VideoSource(320, 240))
    tentativa = asyncio.create_task(
        sala.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    )
    try:
        await asyncio.sleep(2)
        assert await _participantes(ambiente, "integracao-camera") == {f"pc-{FULANO.discord_id}": []}
        assert not tentativa.done() or tentativa.exception() is not None
    finally:
        tentativa.cancel()
        await asyncio.gather(tentativa, return_exceptions=True)
        await sala.disconnect()


async def test_parar_com_o_pc_sem_backend_derruba_a_transmissao_pelo_livekit(ambiente, cliente_pareado):
    """O PC perdeu só o WebSocket e segue transmitindo: o /parar tira ele da sala pelo servidor."""
    cliente = await cliente_pareado(publicar=True)
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("integracao-remover", "480p", 15, 800))
    async with asyncio.timeout(5):
        while not (await _participantes(ambiente, sessao.sala)).get(f"pc-{FULANO.discord_id}"):
            await asyncio.sleep(0.1)

    await cliente._ws.close()  # cai o WebSocket com o backend; a publicação no LiveKit continua
    async with asyncio.timeout(3):
        while ambiente.gateway.online(FULANO.discord_id):
            await asyncio.sleep(0.05)

    parada = await ambiente.servico.parar(FULANO)
    assert not parada.confirmado and parada.derrubado_no_servidor
    assert f"pc-{FULANO.discord_id}" not in await _participantes(ambiente, sessao.sala)
