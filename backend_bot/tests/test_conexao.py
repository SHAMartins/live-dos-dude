"""Handshake, pareamento e ciclo de vida da conexão WebSocket."""

from __future__ import annotations

import json

from websockets.asyncio.client import connect

from app import protocolo as proto
from auxiliares import CICLANO, FULANO, esperar_online
from cliente_falso import ClienteFalso


async def test_pareia_e_reconecta_com_token(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    token = cliente.token
    assert token and len(token) >= 32

    # Só o hash fica no banco.
    dispositivo = await ambiente.repo.dispositivo_do_usuario(FULANO.discord_id)
    assert dispositivo.nome == "PC de Fulano"
    assert await ambiente.repo.dispositivo_por_token(token) is None

    await cliente.fechar()
    await esperar_online(ambiente.gateway, FULANO, online=False)

    de_novo = ClienteFalso(ambiente.url_ws)
    resposta = await de_novo.conectar(token=token)
    assert resposta == {"tipo": "bem_vindo", "usuario": "Fulano", "dispositivo": "PC de Fulano"}
    await esperar_online(ambiente.gateway, FULANO)
    await de_novo.fechar()


async def test_codigo_aceita_minusculas_e_sem_hifen(ambiente):
    codigo = await ambiente.servico.gerar_codigo(FULANO)
    cliente = ClienteFalso(ambiente.url_ws)
    resposta = await cliente.conectar(codigo=codigo.replace("-", "").lower())
    assert resposta["tipo"] == "pareado"
    await cliente.fechar()


async def test_codigo_e_de_uso_unico(ambiente):
    codigo = await ambiente.servico.gerar_codigo(FULANO)
    primeiro = ClienteFalso(ambiente.url_ws)
    assert (await primeiro.conectar(codigo=codigo))["tipo"] == "pareado"

    segundo = ClienteFalso(ambiente.url_ws)
    resposta = await segundo.conectar(codigo=codigo)
    assert resposta["codigo"] == "codigo_invalido"
    assert await segundo.esperar_fechamento() == proto.Fechamento.NAO_AUTENTICADO
    await primeiro.fechar()


async def test_codigo_expirado_e_recusado(ambiente):
    codigo = await ambiente.servico.gerar_codigo(FULANO)
    await ambiente.repo._db.execute("UPDATE codigos_pareamento SET expira_em = 0")
    await ambiente.repo._db.commit()

    cliente = ClienteFalso(ambiente.url_ws)
    resposta = await cliente.conectar(codigo=codigo)
    assert resposta["codigo"] == "codigo_invalido"
    assert await cliente.esperar_fechamento() == proto.Fechamento.NAO_AUTENTICADO


async def test_novo_codigo_invalida_o_anterior(ambiente):
    antigo = await ambiente.servico.gerar_codigo(FULANO)
    await ambiente.servico.gerar_codigo(FULANO)
    cliente = ClienteFalso(ambiente.url_ws)
    assert (await cliente.conectar(codigo=antigo))["codigo"] == "codigo_invalido"


async def test_token_invalido_e_recusado(ambiente):
    cliente = ClienteFalso(ambiente.url_ws)
    resposta = await cliente.conectar(token="x" * 43)
    assert resposta["codigo"] == "token_invalido"
    assert await cliente.esperar_fechamento() == proto.Fechamento.NAO_AUTENTICADO


async def test_versao_incompativel(ambiente):
    async with connect(ambiente.url_ws) as ws:
        await ws.send(json.dumps({"tipo": "hello", "v": 99, "device_token": "x" * 43}))
        resposta = json.loads(await ws.recv())
        assert resposta["codigo"] == "versao_incompativel"
        await ws.wait_closed()
        assert ws.close_code == proto.Fechamento.VERSAO_INCOMPATIVEL


async def test_handshake_malformado(ambiente):
    async with connect(ambiente.url_ws) as ws:
        await ws.send("isto não é json")
        resposta = json.loads(await ws.recv())
        assert resposta["codigo"] == "handshake_invalido"
        await ws.wait_closed()
        assert ws.close_code == proto.Fechamento.PROTOCOLO_INVALIDO


async def test_mensagem_invalida_depois_do_handshake_nao_derruba(ambiente, cliente_pareado):
    cliente = await cliente_pareado()
    await cliente.enviar_bruto('{"tipo": "inventado"}')
    erro = await cliente.esperar("erro")
    assert erro["codigo"] == "mensagem_invalida"
    assert ambiente.gateway.online(FULANO.discord_id)


async def test_mesmo_pc_conectando_de_novo_derruba_a_conexao_antiga(ambiente, cliente_pareado):
    antigo = await cliente_pareado()
    novo = ClienteFalso(ambiente.url_ws)
    assert (await novo.conectar(token=antigo.token))["tipo"] == "bem_vindo"

    assert await antigo.esperar_fechamento() == proto.Fechamento.SUBSTITUIDO
    assert ambiente.gateway.online(FULANO.discord_id)  # a nova continua valendo
    await novo.fechar()


async def test_usuarios_diferentes_ficam_isolados(ambiente, cliente_pareado):
    await cliente_pareado(FULANO)
    await cliente_pareado(CICLANO)
    assert ambiente.gateway.total_online == 2


async def test_saude(ambiente, cliente_pareado):
    await cliente_pareado()
    resposta = await ambiente.app.test_client().get("/saude")
    assert await resposta.get_json() == {"ok": True, "clientes_online": 1, "discord": "desativado"}


async def test_desligar_o_backend_derruba_os_clientes_em_vez_de_travar(tmp_path):
    """Regressão: com um PC conectado, o Hypercorn esperava a conexão fechar para sempre."""
    import asyncio
    import time

    from hypercorn.asyncio import serve

    from app.api.servidor import config_hypercorn, criar_app, gatilho_desligamento
    from app.config import Config
    from app.db.repositorio import Repositorio
    from app.services.gateway import Gateway
    from app.services.tokens import EmissorTokens
    from app.services.transmissao import ServicoTransmissao
    from conftest import _esperar_porta, _porta_livre

    config = Config(_env_file=None, banco=tmp_path / "b.sqlite3", porta=_porta_livre())
    repo = await Repositorio.abrir(config.banco)
    gateway = Gateway()
    servico = ServicoTransmissao(repo, gateway, EmissorTokens.de_config(config), config)
    parar = asyncio.Event()
    servidor = asyncio.create_task(serve(criar_app(servico, gateway, lambda: "x"), config_hypercorn(config),
                                         shutdown_trigger=gatilho_desligamento(parar, gateway)))
    await _esperar_porta(config.porta)
    cliente = ClienteFalso(f"ws://127.0.0.1:{config.porta}/ws/cliente")
    await cliente.conectar(codigo=await servico.gerar_codigo(FULANO))
    await esperar_online(gateway, FULANO)

    inicio = time.perf_counter()
    parar.set()
    await asyncio.wait_for(servidor, 5)
    # Termina dentro do prazo gracioso (os ~3 s vêm da tarefa de ping do Hypercorn, ver conftest).
    assert time.perf_counter() - inicio < config_hypercorn(config).graceful_timeout + 1.5
    assert await cliente.esperar_fechamento() == proto.Fechamento.REINICIANDO
    await repo.fechar()
