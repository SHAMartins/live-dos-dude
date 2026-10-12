"""Fluxo /transmitir -> push 'iniciar' -> ack -> resultado, e o /parar."""

from __future__ import annotations

import asyncio
import time

import pytest
from livekit import api

from app.services.transmissao import ErroTransmissao, PedidoTransmissao
from auxiliares import CICLANO, FULANO, esperar_online
from cliente_falso import ClienteFalso


async def test_transmitir_envia_iniciar_e_ativa_sessao(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev", "1080p", 60, 6000, "h264"))

    assert sessao.estado == "ativa"
    assert (sessao.sala, sessao.resolucao, sessao.fps, sessao.bitrate_kbps, sessao.codec) == \
        ("dev", "1080p", 60, 6000, "h264")

    iniciar = await cliente.esperar("iniciar")
    assert iniciar["id"] == sessao.id
    assert iniciar["livekit_url"] == ambiente.config.livekit_url
    assert iniciar["video"] == {"largura": 1920, "altura": 1080, "fps": 60, "bitrate_kbps": 6000, "codec": "h264"}
    assert cliente.sessao_ativa == sessao.id


async def test_token_do_publicador_so_permite_tela_na_sala_pedida(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    iniciar = await cliente.esperar("iniciar")

    claims = api.TokenVerifier(ambiente.config.livekit_api_key,
                               ambiente.config.livekit_api_secret.get_secret_value()).verify(iniciar["token"])
    assert claims.identity == f"pc-{FULANO.discord_id}"
    assert claims.video.room == "dev"
    assert claims.video.can_publish is True
    assert claims.video.can_subscribe is False
    assert set(claims.video.can_publish_sources) == {"screen_share", "screen_share_audio"}


async def test_parametros_omitidos_vem_da_sala(ambiente, cliente_pareado):
    await cliente_pareado()
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("Geral "))  # normaliza o nome
    assert (sessao.sala, sessao.resolucao, sessao.fps, sessao.bitrate_kbps, sessao.codec) == \
        ("geral", "720p", 30, 2500, "vp8")
    assert await ambiente.servico.sugerir_salas("ge") == ["geral"]


@pytest.mark.parametrize("sala", ["", "com espaço", "a" * 33, "-comeca-com-hifen", "acentuação"])
async def test_nome_de_sala_invalido(ambiente, cliente_pareado, sala):
    await cliente_pareado()
    with pytest.raises(ErroTransmissao, match="Nome de sala inválido"):
        await ambiente.servico.transmitir(FULANO, PedidoTransmissao(sala))


async def test_sem_pc_pareado(ambiente):
    with pytest.raises(ErroTransmissao, match="não pareou"):
        await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))


async def test_pc_offline(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    await cliente.fechar()
    await esperar_online(ambiente.gateway, FULANO, online=False)
    with pytest.raises(ErroTransmissao, match="offline"):
        await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))


async def test_pc_mudo_estoura_o_prazo_do_ack(ambiente, cliente_pareado):
    await cliente_pareado(modo="mudo")
    inicio = time.perf_counter()
    with pytest.raises(ErroTransmissao, match="não respondeu"):
        await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    assert time.perf_counter() - inicio < ambiente.config.timeout_ack_s + 0.5

    status = await ambiente.servico.status(FULANO)
    assert status.sessao is None  # a sessão falhou, não ficou "em andamento"


async def test_pc_que_falha_repassa_o_motivo(ambiente, cliente_pareado):
    await cliente_pareado(modo="falhar")
    with pytest.raises(ErroTransmissao, match="falha simulada"):
        await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))


async def test_ack_sem_resultado_manda_parar_para_nao_deixar_orfa(ambiente, cliente_pareado, monkeypatch):
    cliente = await cliente_pareado()

    async def so_ack(msg):  # confirma, mas nunca termina de iniciar
        await cliente._enviar({"tipo": "ack", "id": msg["id"]})

    monkeypatch.setattr(cliente, "_ao_iniciar", so_ack)
    with pytest.raises(ErroTransmissao, match="não conseguiu iniciar a tempo"):
        await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    iniciar = await cliente.esperar("iniciar")
    parar = await cliente.esperar("parar")
    assert parar["sessao_id"] == iniciar["id"]


async def test_conexao_cai_no_meio_do_pedido(ambiente, cliente_pareado, monkeypatch):
    cliente = await cliente_pareado()

    async def cai(_msg):
        await cliente._ws.close()

    monkeypatch.setattr(cliente, "_ao_iniciar", cai)
    with pytest.raises(ErroTransmissao, match="caiu"):
        await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))


async def test_nao_transmite_duas_vezes(ambiente, cliente_pareado):
    await cliente_pareado()
    await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    with pytest.raises(ErroTransmissao, match="já está transmitindo"):
        await ambiente.servico.transmitir(FULANO, PedidoTransmissao("outra"))


async def test_dois_pedidos_simultaneos_so_um_passa(ambiente, cliente_pareado):
    await cliente_pareado()
    resultados = await asyncio.gather(
        ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev")),
        ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev")),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ErroTransmissao) for r in resultados) == 1


async def test_parar_confirma_e_encerra(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))

    parada = await ambiente.servico.parar(FULANO)
    assert parada.confirmado and parada.sessao.id == sessao.id and not parada.derrubado_no_servidor
    assert (await cliente.esperar("parar"))["sessao_id"] == sessao.id
    assert cliente.sessao_ativa is None
    assert (await ambiente.repo.sessao(sessao.id)).estado == "encerrada"


async def test_parar_com_pc_offline_encerra_sem_confirmacao(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.fechar()
    await esperar_online(ambiente.gateway, FULANO, online=False)

    parada = await ambiente.servico.parar(FULANO)
    assert not parada.confirmado
    assert (await ambiente.repo.sessao(parada.sessao.id)).estado == "encerrada"


async def test_parar_sem_transmissao(ambiente, cliente_pareado):
    await cliente_pareado()
    with pytest.raises(ErroTransmissao, match="não está transmitindo"):
        await ambiente.servico.parar(FULANO)


async def test_usuario_para_pela_bandeja(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.encerrar_pela_bandeja()

    async with asyncio.timeout(2):
        while (await ambiente.repo.sessao(sessao.id)).estado != "encerrada":
            await asyncio.sleep(0.01)
    assert (await ambiente.repo.sessao(sessao.id)).detalhe == "parado pela bandeja"


async def test_reconexao_mantem_a_sessao_que_o_cliente_ainda_transmite(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.fechar()
    await esperar_online(ambiente.gateway, FULANO, online=False)

    de_volta = ClienteFalso(ambiente.url_ws)
    de_volta.sessao_ativa = sessao.id  # caiu só o WebSocket; o vídeo seguiu no ar
    await de_volta.conectar(token=cliente.token)
    await esperar_online(ambiente.gateway, FULANO)
    assert (await ambiente.servico.status(FULANO)).sessao.id == sessao.id
    await de_volta.fechar()


async def test_reconexao_sem_sessao_encerra_a_do_banco(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente.fechar()
    await esperar_online(ambiente.gateway, FULANO, online=False)

    de_volta = ClienteFalso(ambiente.url_ws)  # o app reiniciou: não transmite mais nada
    await de_volta.conectar(token=cliente.token)
    await esperar_online(ambiente.gateway, FULANO)
    assert (await ambiente.repo.sessao(sessao.id)).estado == "encerrada"
    await de_volta.fechar()


async def test_cliente_nao_encerra_sessao_de_outro_usuario(ambiente, cliente_pareado):
    await cliente_pareado(FULANO)
    intruso = await cliente_pareado(CICLANO)
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))

    intruso.sessao_ativa = sessao.id
    await intruso.encerrar_pela_bandeja()
    await asyncio.sleep(0.1)
    assert (await ambiente.repo.sessao(sessao.id)).estado == "ativa"


async def test_iniciar_diz_quais_parametros_vieram_do_comando(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev", fps=60, codec="h264"))
    iniciar = await cliente.esperar("iniciar")
    assert set(iniciar["definidos"]) == {"fps", "codec"}
    assert iniciar["video"]["fps"] == 60


async def test_parametros_aplicados_pelo_pc_ficam_na_sessao(ambiente, cliente_pareado, monkeypatch):
    cliente = await cliente_pareado()

    async def aplica_os_padroes_do_pc(msg):
        await cliente._enviar({"tipo": "ack", "id": msg["id"]})
        await cliente._enviar({"tipo": "resultado", "id": msg["id"], "ok": True,
                               "video": {"largura": 854, "altura": 480, "fps": 15, "bitrate_kbps": 900, "codec": "vp8"}})

    monkeypatch.setattr(cliente, "_ao_iniciar", aplica_os_padroes_do_pc)
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    assert (sessao.resolucao, sessao.fps, sessao.bitrate_kbps) == ("854x480", 15, 900)


async def test_pc_avisa_que_mudou_a_qualidade_no_meio(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await cliente._enviar({"tipo": "transmissao_atualizada", "sessao_id": sessao.id,
                           "video": {"largura": 1920, "altura": 1080, "fps": 60, "bitrate_kbps": 7000, "codec": "vp8"}})
    async with asyncio.timeout(2):
        while (await ambiente.repo.sessao(sessao.id)).fps != 60:
            await asyncio.sleep(0.02)
    assert (await ambiente.repo.sessao(sessao.id)).resolucao == "1920x1080"


async def test_pc_nao_altera_sessao_de_outro_usuario(ambiente, cliente_pareado):
    await cliente_pareado(FULANO)
    intruso = await cliente_pareado(CICLANO)
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    await intruso._enviar({"tipo": "transmissao_atualizada", "sessao_id": sessao.id,
                           "video": {"largura": 1, "altura": 1, "fps": 1, "bitrate_kbps": 1, "codec": "vp8"}})
    await asyncio.sleep(0.2)
    assert (await ambiente.repo.sessao(sessao.id)).fps == 30
