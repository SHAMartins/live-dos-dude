from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import pytest
from hypercorn.asyncio import serve

from app.api.servidor import config_hypercorn, criar_app, gatilho_desligamento
from app.config import Config
from app.db.repositorio import Repositorio
from app.services.gateway import Gateway
from app.services.tokens import EmissorTokens
from app.services.transmissao import ServicoTransmissao, Usuario
from auxiliares import FULANO, esperar_online
from cliente_falso import ClienteFalso


@dataclass
class Ambiente:
    config: Config
    repo: Repositorio
    gateway: Gateway
    servico: ServicoTransmissao
    url_ws: str
    app: object


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _esperar_porta(porta: int) -> None:
    async with asyncio.timeout(5):
        while True:
            try:
                _, escrita = await asyncio.open_connection("127.0.0.1", porta)
                escrita.close()
                await escrita.wait_closed()
                return
            except OSError:
                await asyncio.sleep(0.02)


@pytest.fixture
async def ambiente(tmp_path) -> AsyncIterator[Ambiente]:
    """Backend real (Hypercorn + Quart) numa porta livre, com banco temporário e prazos curtos."""
    porta = _porta_livre()
    config = Config(_env_file=None, banco=tmp_path / "teste.sqlite3", porta=porta,
                    timeout_ack_s=0.5, timeout_inicio_s=1.0)
    repo = await Repositorio.abrir(config.banco)
    gateway = Gateway()
    servico = ServicoTransmissao(repo, gateway, EmissorTokens.de_config(config), config)
    app = criar_app(servico, gateway, lambda: "desativado")
    hc = config_hypercorn(config)
    # A tarefa de ping do Hypercorn só percebe o fechamento quando acorda (a cada 20 s),
    # então o desligamento sempre esgota o graceful_timeout. Em produção 3 s é ok; aqui atrasaria cada teste.
    hc.graceful_timeout = 0.1
    parar = asyncio.Event()
    servidor = asyncio.create_task(serve(app, hc, shutdown_trigger=gatilho_desligamento(parar, gateway)))
    await _esperar_porta(porta)
    try:
        yield Ambiente(config, repo, gateway, servico, f"ws://127.0.0.1:{porta}/ws/cliente", app)
    finally:
        parar.set()
        await servidor
        await repo.fechar()


CriarCliente = Callable[..., Awaitable[ClienteFalso]]


@pytest.fixture
async def cliente_pareado(ambiente: Ambiente) -> AsyncIterator[CriarCliente]:
    """Fábrica: gera o código pelo serviço (como o /parear faria) e pareia um cliente falso."""
    criados: list[ClienteFalso] = []

    async def criar(usuario: Usuario = FULANO, **opcoes) -> ClienteFalso:
        codigo = await ambiente.servico.gerar_codigo(usuario)
        cliente = ClienteFalso(ambiente.url_ws, **opcoes)
        resposta = await cliente.conectar(codigo=codigo, nome=f"PC de {usuario.nome}")
        assert resposta["tipo"] == "pareado", resposta
        await esperar_online(ambiente.gateway, usuario)
        criados.append(cliente)
        return cliente

    yield criar
    for cliente in criados:
        await cliente.fechar()

