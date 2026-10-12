"""Registro das conexões dos clientes desktop e correlação comando -> ack -> resultado."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from app import protocolo as proto
from app.db.repositorio import Dispositivo

log = logging.getLogger(__name__)


class ClienteOffline(Exception):
    pass


class ConexaoPerdida(Exception):
    pass


class SemResposta(Exception):
    def __init__(self, fase: str) -> None:
        super().__init__(f"sem '{fase}' do cliente")
        self.fase = fase  # "ack" ou "resultado"


@dataclass
class _Pedido:
    # Events em vez de Futures: perder a conexão não deixa exceções "nunca lidas".
    confirmado: asyncio.Event = field(default_factory=asyncio.Event)
    concluido: asyncio.Event = field(default_factory=asyncio.Event)
    resultado: proto.Resultado | None = None
    conexao_perdida: bool = False


class ConexaoCliente:
    """Um cliente autenticado. Tudo que sai passa pela fila, consumida pelo handler do WebSocket."""

    def __init__(self, dispositivo: Dispositivo) -> None:
        self.dispositivo = dispositivo
        self._saida: asyncio.Queue[str | tuple[int, str]] = asyncio.Queue()
        self._pendentes: dict[str, _Pedido] = {}

    @property
    def discord_id(self) -> int:
        return self.dispositivo.discord_id

    def enviar(self, mensagem: BaseModel) -> None:
        self._saida.put_nowait(mensagem.model_dump_json())

    def fechar(self, codigo: int, motivo: str) -> None:
        self._saida.put_nowait((codigo, motivo))

    async def proxima_saida(self) -> str | tuple[int, str]:
        return await self._saida.get()

    def receber(self, mensagem: proto.Ack | proto.Resultado) -> None:
        pedido = self._pendentes.get(mensagem.id)
        if pedido is None:
            log.warning("resposta para pedido desconhecido ou expirado: %s", mensagem.id)
            return
        pedido.confirmado.set()  # um 'resultado' também vale como ack
        if isinstance(mensagem, proto.Resultado):
            pedido.resultado = mensagem
            pedido.concluido.set()

    def encerrar(self) -> None:
        """Acorda quem espera resposta desta conexão: ela não vai chegar."""
        for pedido in self._pendentes.values():
            pedido.conexao_perdida = True
            pedido.confirmado.set()
            pedido.concluido.set()
        self._pendentes.clear()

    async def comando(self, mensagem: proto.Comando, timeout_ack: float,
                      timeout_resultado: float) -> proto.Resultado:
        pedido = _Pedido()
        self._pendentes[mensagem.id] = pedido
        try:
            self.enviar(mensagem)
            await _esperar(pedido, pedido.confirmado, timeout_ack, "ack")
            await _esperar(pedido, pedido.concluido, timeout_resultado, "resultado")
            assert pedido.resultado is not None
            return pedido.resultado
        finally:
            self._pendentes.pop(mensagem.id, None)


async def _esperar(pedido: _Pedido, evento: asyncio.Event, timeout: float, fase: str) -> None:
    try:
        await asyncio.wait_for(evento.wait(), timeout)
    except TimeoutError:
        raise SemResposta(fase) from None
    if pedido.conexao_perdida:
        raise ConexaoPerdida()


class Gateway:
    def __init__(self) -> None:
        self._conexoes: dict[int, ConexaoCliente] = {}

    @property
    def total_online(self) -> int:
        return len(self._conexoes)

    def online(self, discord_id: int) -> bool:
        return discord_id in self._conexoes

    def conexao(self, discord_id: int) -> ConexaoCliente:
        try:
            return self._conexoes[discord_id]
        except KeyError:
            raise ClienteOffline() from None

    def registrar(self, conexao: ConexaoCliente) -> None:
        antiga = self._conexoes.get(conexao.discord_id)
        if antiga is not None:
            log.info("PC de %s conectou de novo; derrubando a conexão antiga", conexao.discord_id)
            antiga.fechar(proto.Fechamento.SUBSTITUIDO, "substituido")
            antiga.encerrar()
        self._conexoes[conexao.discord_id] = conexao

    def fechar_todas(self, codigo: int, motivo: str) -> None:
        for conexao in list(self._conexoes.values()):
            conexao.fechar(codigo, motivo)

    def remover(self, conexao: ConexaoCliente) -> None:
        if self._conexoes.get(conexao.discord_id) is conexao:
            del self._conexoes[conexao.discord_id]
        conexao.encerrar()

    async def comando(self, discord_id: int, mensagem: proto.Comando,
                      timeout_ack: float, timeout_resultado: float) -> proto.Resultado:
        return await self.conexao(discord_id).comando(mensagem, timeout_ack, timeout_resultado)
