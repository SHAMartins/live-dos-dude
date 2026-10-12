"""Peças dos testes: backend real controlável, publicador falso e cliente com histórico de estados."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field

from hypercorn.asyncio import serve

from app.api.servidor import config_hypercorn, criar_app, gatilho_desligamento
from app.config import Config as ConfigBackend
from app.db.repositorio import Repositorio
from app.services.gateway import Gateway
from app.services.tokens import EmissorTokens
from app.services.transmissao import ServicoTransmissao, Usuario
from cliente import protocolo as proto
from cliente.conexao import Backoff
from cliente.config import ConfigCliente
from cliente.controlador import Controlador, ParametrosMidia
from cliente.credenciais import CredenciaisMemoria
from cliente.estado import Estado, Snapshot

FULANO = Usuario(discord_id=111, nome="Fulano")


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Backend:
    """Backend real; pode ser derrubado e religado na mesma porta e com o mesmo banco."""

    def __init__(self, banco, timeout_inicio_s: float = 3.0) -> None:
        self.porta = _porta_livre()
        self.config = ConfigBackend(_env_file=None, banco=banco, porta=self.porta,
                                    timeout_ack_s=1.0, timeout_inicio_s=timeout_inicio_s)
        self.url = f"ws://127.0.0.1:{self.porta}/ws/cliente"
        self._parar: asyncio.Event | None = None
        self._tarefa: asyncio.Task | None = None

    async def ligar(self) -> None:
        self.repo = await Repositorio.abrir(self.config.banco)
        self.gateway = Gateway()
        self.servico = ServicoTransmissao(self.repo, self.gateway, EmissorTokens.de_config(self.config), self.config)
        hc = config_hypercorn(self.config)
        hc.graceful_timeout = 0.1  # ver backend_bot/tests/conftest.py
        self._parar = asyncio.Event()
        self._tarefa = asyncio.create_task(serve(criar_app(self.servico, self.gateway, lambda: "desativado"),
                                                 hc, shutdown_trigger=gatilho_desligamento(self._parar, self.gateway)))
        async with asyncio.timeout(5):
            while True:
                try:
                    _, escrita = await asyncio.open_connection("127.0.0.1", self.porta)
                    escrita.close()
                    return
                except OSError:
                    await asyncio.sleep(0.02)

    async def desligar(self) -> None:
        if self._tarefa is not None:
            self._parar.set()
            await self._tarefa
            await self.repo.fechar()
            self._tarefa = None


@dataclass
class InfoFalsa:
    sala: str
    largura: int
    altura: int
    fps: int
    bitrate_kbps: int
    codec: str
    captura: str = "falsa"
    fonte: str = "fonte falsa"


@dataclass
class Iniciado:
    sala: str
    parametros: ParametrosMidia
    fonte: dict | None


@dataclass
class PublicadorFalso:
    """Faz o papel da mídia: registra as chamadas e pode falhar ou 'cair' sob comando.

    A "fonte" tem o tamanho 1920x1080, então a resolução aplicada é a caixa pedida.
    """

    falhar_com: str | None = None
    falhar_na_troca: str | None = None
    iniciados: list[Iniciado] = field(default_factory=list)
    trocas: list[Iniciado] = field(default_factory=list)
    paradas: int = 0
    ao_cair: object = None
    _sala: str = ""

    async def iniciar(self, livekit_url, token, nome_sala, parametros: ParametrosMidia, fonte, ao_cair) -> InfoFalsa:
        if self.falhar_com:
            raise RuntimeError(self.falhar_com)
        self.iniciados.append(Iniciado(nome_sala, parametros, fonte))
        self.ao_cair, self._sala = ao_cair, nome_sala
        return self._info(parametros)

    async def trocar(self, parametros: ParametrosMidia, fonte) -> InfoFalsa:
        if self.falhar_na_troca:
            raise RuntimeError(self.falhar_na_troca)
        self.trocas.append(Iniciado(self._sala, parametros, fonte))
        return self._info(parametros)

    async def parar(self) -> None:
        self.paradas += 1

    def _info(self, p: ParametrosMidia) -> InfoFalsa:
        largura, altura = min(p.alvo_largura, 1920), min(p.alvo_altura, 1080)
        return InfoFalsa(self._sala, largura, altura, p.fps, p.bitrate_kbps, p.codec)


class ClienteDeTeste:
    """Controlador rodando numa tarefa, com o histórico de snapshots para as asserções."""

    def __init__(self, url: str, credenciais: CredenciaisMemoria, publicador,
                 config: ConfigCliente | None = None) -> None:
        self.snapshots: list[Snapshot] = []
        self._mudou = asyncio.Event()
        self.credenciais = credenciais
        self.publicador = publicador
        self.controlador = Controlador(
            config or ConfigCliente(backend_url=url, nome_dispositivo="PC de teste"), credenciais, publicador,
            self._registrar, backoff=Backoff(inicial=0.1, maximo=0.5),
        )
        self._tarefa = asyncio.create_task(self.controlador.executar())

    def _registrar(self, snapshot: Snapshot) -> None:
        self.snapshots.append(snapshot)
        self._mudou.set()

    @property
    def estado(self) -> Estado:
        return self.snapshots[-1].estado if self.snapshots else Estado.INICIANDO

    async def esperar(self, estado: Estado, timeout: float = 5.0) -> Snapshot:
        async with asyncio.timeout(timeout):
            while self.estado != estado:
                self._mudou.clear()
                await self._mudou.wait()
        return self.snapshots[-1]

    async def fechar(self) -> None:
        await self.controlador.encerrar()
        await asyncio.wait_for(self._tarefa, 5)


async def parear(backend: Backend, cliente: ClienteDeTeste, usuario: Usuario = FULANO) -> Snapshot:
    await cliente.esperar(Estado.PRECISA_PAREAR)
    cliente.controlador.parear(await backend.servico.gerar_codigo(usuario))
    return await cliente.esperar(Estado.ONLINE)
