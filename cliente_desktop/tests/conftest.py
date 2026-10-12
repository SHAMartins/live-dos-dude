"""Os testes sobem o BACKEND REAL (backend_bot) numa porta livre e põem o cliente para conversar com ele."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from auxiliares import Backend, ClienteDeTeste, PublicadorFalso
from cliente.config import ConfigCliente
from cliente.credenciais import CredenciaisMemoria


@pytest.fixture
async def backend(tmp_path) -> AsyncIterator[Backend]:
    b = Backend(tmp_path / "backend.sqlite3")
    await b.ligar()
    yield b
    await b.desligar()


@pytest.fixture
async def criar_cliente(backend):
    criados: list[ClienteDeTeste] = []

    def criar(credenciais: CredenciaisMemoria | None = None, publicador=None, url: str | None = None,
              **padroes) -> ClienteDeTeste:
        config = ConfigCliente(backend_url=url or backend.url, nome_dispositivo="PC de teste", **padroes)
        cliente = ClienteDeTeste(config.backend_url, credenciais or CredenciaisMemoria(),
                                 publicador or PublicadorFalso(), config)
        criados.append(cliente)
        return cliente

    yield criar
    for cliente in criados:
        await cliente.fechar()
