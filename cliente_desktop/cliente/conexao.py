"""WebSocket com o backend: handshake, recepção de comandos e reconexão. Contrato: docs/protocolo.md."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from cliente import VERSAO
from cliente import protocolo as proto
from cliente.credenciais import Credenciais
from cliente.estado import Estado

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Identidade:
    usuario: str
    dispositivo: str


class Backoff:
    """1 s, 2 s, 4 s ... até 30 s, com variação aleatória para os clientes não voltarem todos juntos."""

    def __init__(self, inicial: float = 1.0, maximo: float = 30.0) -> None:
        self.inicial, self.maximo = inicial, maximo
        self._atual = inicial

    def proximo(self) -> float:
        espera = self._atual * random.uniform(0.8, 1.2)
        self._atual = min(self._atual * 2, self.maximo)
        return espera

    def zerar(self) -> None:
        self._atual = self.inicial


class _Fechado(Exception):
    def __init__(self, codigo: int | None, mensagem: str = "") -> None:
        super().__init__(f"fechado ({codigo}): {mensagem}")
        self.codigo, self.mensagem = codigo, mensagem


class ConexaoBackend:
    def __init__(
        self,
        url: str,
        credenciais: Credenciais,
        nome_dispositivo: str,
        *,
        ao_comando: Callable[[proto.Comando], Awaitable[None]],
        ao_estado: Callable[[Estado, str, Identidade | None], None],
        sessao_ativa: Callable[[], str | None],
        backoff: Backoff | None = None,
    ) -> None:
        self.url = url
        self.nome_dispositivo = nome_dispositivo
        self._credenciais = credenciais
        self._ao_comando = ao_comando
        self._ao_estado = ao_estado
        self._sessao_ativa = sessao_ativa
        self._backoff = backoff or Backoff()
        self._ws: ClientConnection | None = None
        self._codigo: str | None = None
        self._acordar = asyncio.Event()  # pareamento pedido, "reconectar agora" ou encerramento
        self._encerrando = False
        self._tarefas: set[asyncio.Task] = set()

    # --- ações vindas da UI (sempre agendadas na thread asyncio) -------------

    def parear(self, codigo: str) -> None:
        self._codigo = codigo.strip()
        self._acordar.set()
        self._derrubar()  # se estava conectado com outro token, reconecta usando o código

    def reconectar(self) -> None:
        self._acordar.set()
        self._derrubar()

    def trocar_destino(self, url: str, nome_dispositivo: str) -> None:
        self.url, self.nome_dispositivo = url, nome_dispositivo
        self.reconectar()

    async def encerrar(self) -> None:
        self._encerrando = True
        self._acordar.set()
        if self._ws is not None:
            await self._ws.close()

    async def enviar(self, mensagem: BaseModel) -> bool:
        """Envia se conectado. Sem conexão a mensagem se perde, e tudo bem: o backend tem
        prazo para ack/resultado e, ao reconectar, o 'hello' reconcilia a sessão ativa."""
        ws = self._ws
        if ws is None:
            log.warning("sem conexão; descartando %s", mensagem.__class__.__name__)
            return False
        try:
            await ws.send(mensagem.model_dump_json())
            return True
        except ConnectionClosed:
            return False

    # --- laço principal -------------------------------------------------------

    async def executar(self) -> None:
        while not self._encerrando:
            self._acordar.clear()
            token = self._credenciais.ler(self.url)
            if token is None and self._codigo is None:
                self._ao_estado(Estado.PRECISA_PAREAR, "Digite o código gerado pelo /parear no Discord.", None)
                await self._acordar.wait()
                continue

            codigo_usado = self._codigo
            self._ao_estado(Estado.CONECTANDO, f"Conectando a {self.url}...", None)
            try:
                await self._sessao(token)
                continue  # fechamento normal (ex.: pedimos para reconectar): tenta de novo na hora
            except _Fechado as fechado:
                if await self._tratar_fechamento(fechado, codigo_usado):
                    continue
            except (OSError, TimeoutError, InvalidURI, InvalidHandshake) as erro:
                log.info("falha ao conectar: %s", erro)
            if self._encerrando:
                break
            espera = self._backoff.proximo()
            self._ao_estado(Estado.CONECTANDO, f"Sem conexão com o backend. Nova tentativa em {espera:.0f} s.", None)
            await self._dormir(espera)

    async def _sessao(self, token: str | None) -> None:
        async with connect(self.url, open_timeout=10, ping_interval=20, ping_timeout=20,
                           max_size=64 * 1024) as ws:
            self._ws = ws
            try:
                identidade = await self._handshake(ws, token)
                self._backoff.zerar()
                self._ao_estado(Estado.ONLINE, "", identidade)
                await self._receber(ws)
            except ConnectionClosed as erro:
                raise _Fechado(erro.rcvd.code if erro.rcvd else None) from None
            finally:
                # Comandos em andamento NÃO são cancelados: um 'iniciar' interrompido no meio
                # deixaria a publicação pela metade. Eles terminam e o 'resultado' só não chega.
                self._ws = None

    async def _handshake(self, ws: ClientConnection, token: str | None) -> Identidade:
        if self._codigo is not None:
            hello: BaseModel = proto.Parear(v=proto.VERSAO, codigo=self._codigo, nome_dispositivo=self.nome_dispositivo)
        else:
            hello = proto.Hello(v=proto.VERSAO, device_token=token, versao_cliente=VERSAO,
                                sessao_ativa=self._sessao_ativa())
        await ws.send(hello.model_dump_json())
        resposta = proto.DO_SERVIDOR.validate_json(await asyncio.wait_for(ws.recv(), 10))

        if isinstance(resposta, proto.Erro):
            await ws.wait_closed()
            raise _Fechado(ws.close_code, resposta.mensagem)
        if isinstance(resposta, proto.Pareado):
            self._credenciais.salvar(self.url, resposta.device_token)
            self._codigo = None
            log.info("pareado como '%s' de %s", resposta.dispositivo, resposta.usuario)
        elif not isinstance(resposta, proto.BemVindo):
            raise _Fechado(None, f"resposta inesperada ao handshake: {resposta.tipo}")
        return Identidade(resposta.usuario, resposta.dispositivo)

    async def _receber(self, ws: ClientConnection) -> None:
        async for bruto in ws:
            try:
                mensagem = proto.DO_SERVIDOR.validate_json(bruto)
            except ValidationError:
                log.warning("mensagem desconhecida do backend: %.200s", bruto)
                continue
            if isinstance(mensagem, (proto.Iniciar, proto.Parar)):
                # Em tarefa separada: um 'parar' precisa poder chegar enquanto o 'iniciar' ainda roda.
                tarefa = asyncio.create_task(self._ao_comando(mensagem))
                self._tarefas.add(tarefa)
                tarefa.add_done_callback(self._tarefas.discard)
            elif isinstance(mensagem, proto.Erro):
                log.warning("backend avisou: %s (%s)", mensagem.mensagem, mensagem.codigo)

    async def _tratar_fechamento(self, fechado: _Fechado, codigo_usado: str | None) -> bool:
        """Devolve True se já resolveu (não precisa esperar o backoff)."""
        codigo = fechado.codigo
        if codigo == proto.Fechamento.NAO_AUTENTICADO:
            if codigo_usado is not None:
                if self._codigo == codigo_usado:  # não apaga um código novo digitado nesse meio-tempo
                    self._codigo = None
                self._ao_estado(Estado.PRECISA_PAREAR, "Código inválido ou expirado. Gere outro com /parear.", None)
            else:
                self._credenciais.apagar(self.url)
                self._ao_estado(Estado.PRECISA_PAREAR, "Este PC não é mais reconhecido. Pareie de novo com /parear.", None)
            await self._acordar.wait()
            return True
        mensagens_bloqueio = {
            proto.Fechamento.SUBSTITUIDO: "Este PC conectou em outro lugar (outra instância aberta?).",
            proto.Fechamento.VERSAO_INCOMPATIVEL: "Versão do cliente incompatível com o backend. Atualize o cliente.",
            proto.Fechamento.PROTOCOLO_INVALIDO: "Erro de protocolo com o backend (veja o log).",
        }
        if codigo in mensagens_bloqueio:
            self._ao_estado(Estado.BLOQUEADO, mensagens_bloqueio[codigo], None)
            await self._acordar.wait()  # só volta quando o usuário pedir para reconectar
            return True
        log.info("conexão fechada (%s); vai reconectar", codigo)
        return False

    async def _dormir(self, segundos: float) -> None:
        try:
            await asyncio.wait_for(self._acordar.wait(), segundos)
        except TimeoutError:
            pass

    def _derrubar(self) -> None:
        if self._ws is not None:
            asyncio.ensure_future(self._ws.close())
