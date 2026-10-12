"""Cérebro do cliente (roda na thread asyncio): comandos do backend, publicação e estado para a UI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from cliente import protocolo as proto
from cliente.conexao import Backoff, ConexaoBackend, Identidade
from cliente.config import CAIXAS, ConfigCliente
from cliente.credenciais import Credenciais
from cliente.estado import Estado, Snapshot

log = logging.getLogger(__name__)

# Abaixo dos 20 s que o backend espera pelo 'resultado', para a resposta chegar antes do prazo dele.
TIMEOUT_INICIO_S = 15.0


@dataclass(frozen=True)
class ParametrosMidia:
    alvo_largura: int
    alvo_altura: int
    fps: int
    bitrate_kbps: int
    codec: str
    audio: bool = True


class InfoPublicacao(Protocol):
    sala: str
    largura: int
    altura: int
    fps: int
    bitrate_kbps: int
    codec: str
    captura: str
    fonte: str
    audio: bool


class Publicador(Protocol):
    async def iniciar(self, livekit_url: str, token: str, nome_sala: str, parametros: ParametrosMidia,
                      fonte: dict | None, ao_cair: Callable[[str], None]) -> InfoPublicacao: ...
    async def trocar(self, parametros: ParametrosMidia, fonte: dict | None) -> InfoPublicacao: ...
    async def parar(self) -> None: ...


def mesclar(comando: proto.Iniciar, config: ConfigCliente) -> ParametrosMidia:
    """O que foi digitado no /transmitir vale; o resto vem dos padrões deste PC.

    Backend antigo (sem 'definidos'): tudo veio do comando, como antes.
    """
    definidos = set(comando.definidos) if comando.definidos is not None else {"resolucao", "fps", "bitrate_kbps", "codec"}
    v = comando.video
    largura, altura = (v.largura, v.altura) if "resolucao" in definidos else CAIXAS[config.resolucao]
    return ParametrosMidia(
        alvo_largura=largura,
        alvo_altura=altura,
        fps=v.fps if "fps" in definidos else config.fps,
        bitrate_kbps=v.bitrate_kbps if "bitrate_kbps" in definidos else config.bitrate_kbps,
        codec=v.codec,
        audio=config.audio,
    )


def parametros_do_app(config: ConfigCliente, codec: str) -> ParametrosMidia:
    largura, altura = CAIXAS[config.resolucao]
    return ParametrosMidia(largura, altura, config.fps, config.bitrate_kbps, codec, config.audio)


def video_aplicado(info: InfoPublicacao) -> proto.ParametrosVideo:
    return proto.ParametrosVideo(largura=info.largura, altura=info.altura, fps=info.fps,
                                 bitrate_kbps=info.bitrate_kbps, codec=info.codec)


class Controlador:
    def __init__(self, config: ConfigCliente, credenciais: Credenciais, publicador: Publicador,
                 notificar: Callable[[Snapshot], None], *, backoff: Backoff | None = None) -> None:
        self.config = config
        self.publicador = publicador
        self._notificar = notificar
        self._snapshot = Snapshot()
        self._estado_conexao = Estado.INICIANDO  # o estado exibido é este, ou TRANSMITINDO por cima
        self._sessao_id: str | None = None
        self._info: InfoPublicacao | None = None
        self._trava = asyncio.Lock()  # um comando de mídia por vez (iniciar/parar/parada local)
        self.conexao = ConexaoBackend(
            config.backend_url, credenciais, config.nome_dispositivo,
            ao_comando=self._ao_comando, ao_estado=self._ao_estado_conexao,
            sessao_ativa=lambda: self._sessao_id, backoff=backoff,
        )

    @property
    def sessao_id(self) -> str | None:
        return self._sessao_id

    async def executar(self) -> None:
        await self.conexao.executar()

    # --- ações da UI ----------------------------------------------------------

    def parear(self, codigo: str) -> None:
        self.conexao.parear(codigo)

    def reconectar(self) -> None:
        self.conexao.reconectar()

    def trocar_destino(self, backend_url: str, nome_dispositivo: str) -> None:
        self.config.backend_url, self.config.nome_dispositivo = backend_url, nome_dispositivo
        self.conexao.trocar_destino(backend_url, nome_dispositivo)

    async def parar_local(self, motivo: str = "parado pelo usuário no PC") -> None:
        """Parada pela bandeja/janela: encerra e avisa o backend."""
        async with self._trava:
            sessao = self._sessao_id
            if sessao is None:
                return
            await self._encerrar_publicacao()
        await self.conexao.enviar(proto.TransmissaoEncerrada(sessao_id=sessao, motivo=motivo))

    async def aplicar_configuracao(self) -> None:
        """Botão 'Aplicar agora': troca fonte/qualidade da transmissão no ar pelas do app."""
        async with self._trava:
            if self._sessao_id is None or self._info is None:
                return
            sessao = self._sessao_id
            try:
                info = await self.publicador.trocar(parametros_do_app(self.config, self._info.codec), self.config.fonte)
            except Exception as erro:  # noqa: BLE001 - a fonte nova falhou: encerra em vez de ficar sem vídeo
                log.exception("falha ao aplicar a nova configuração")
                await self._encerrar_publicacao()
                await self.conexao.enviar(proto.TransmissaoEncerrada(
                    sessao_id=sessao, motivo=f"falha ao trocar a fonte: {erro}"[:200]))
                self._atualizar(mensagem=f"Não deu para aplicar: {erro}")
                return
            self._mostrar_publicacao(info)
        await self.conexao.enviar(proto.TransmissaoAtualizada(sessao_id=sessao, video=video_aplicado(info)))

    async def encerrar(self) -> None:
        await self.parar_local("cliente desktop fechado")
        await self.conexao.encerrar()

    # --- comandos do backend --------------------------------------------------

    async def _ao_comando(self, comando: proto.Comando) -> None:
        # O ack sai antes de qualquer espera: o backend dá só 5 s para ele.
        await self.conexao.enviar(proto.Ack(id=comando.id))
        async with self._trava:
            if isinstance(comando, proto.Iniciar):
                resultado = await self._iniciar(comando)
            else:
                resultado = await self._parar_por_comando(comando)
        await self.conexao.enviar(resultado)

    async def _iniciar(self, comando: proto.Iniciar) -> proto.Resultado:
        if self._sessao_id is not None:
            log.info("pedido de nova transmissão com outra ativa (%s); trocando", self._sessao_id)
            await self._encerrar_publicacao()
        try:
            info = await asyncio.wait_for(
                self.publicador.iniciar(comando.livekit_url, comando.token, comando.sala,
                                        mesclar(comando, self.config), self.config.fonte, ao_cair=self._ao_cair),
                TIMEOUT_INICIO_S)
        except Exception as erro:  # noqa: BLE001 - qualquer falha vira 'resultado' com o motivo
            log.exception("falha ao iniciar a transmissão")
            detalhe = "demorou demais para conectar ao LiveKit" if isinstance(erro, TimeoutError) else str(erro)
            return proto.Resultado(id=comando.id, ok=False, detalhe=detalhe[:300] or type(erro).__name__)

        self._sessao_id = comando.id
        self._mostrar_publicacao(info)
        return proto.Resultado(id=comando.id, ok=True, video=video_aplicado(info))

    def _mostrar_publicacao(self, info: InfoPublicacao) -> None:
        self._info = info
        self._atualizar(estado=Estado.TRANSMITINDO, mensagem="", sala=info.sala,
                        resolucao=(info.largura, info.altura), fps_alvo=info.fps, bitrate_kbps=info.bitrate_kbps,
                        codec=info.codec, captura=info.captura, fonte=info.fonte, audio=info.audio)

    async def _parar_por_comando(self, comando: proto.Parar) -> proto.Resultado:
        await self._encerrar_publicacao()
        return proto.Resultado(id=comando.id, ok=True)

    def _ao_cair(self, motivo: str) -> None:
        """Chamado pelo publicador (já na thread asyncio) quando a mídia cai sozinha."""
        log.warning("transmissão caiu: %s", motivo)
        asyncio.ensure_future(self.parar_local(motivo))

    async def _encerrar_publicacao(self) -> None:
        if self._sessao_id is None:
            return
        try:
            await self.publicador.parar()
        except Exception:  # noqa: BLE001 - parar nunca pode deixar o estado preso em "transmitindo"
            log.exception("erro ao parar a publicação")
        self._sessao_id, self._info = None, None
        self._atualizar(estado=self._estado_conexao, sala=None, resolucao=None, fps_alvo=None,
                        bitrate_kbps=None, codec=None, captura=None, fonte=None, audio=None)

    # --- estado ---------------------------------------------------------------

    def _ao_estado_conexao(self, estado: Estado, mensagem: str, identidade: Identidade | None) -> None:
        self._estado_conexao = estado
        # Cair o WebSocket não derruba o vídeo: se ainda transmite, continua "transmitindo"
        # (a mensagem avisa que o backend está fora).
        exibido = Estado.TRANSMITINDO if self._sessao_id is not None else estado
        mudancas: dict = {"estado": exibido, "mensagem": mensagem}
        if identidade is not None:
            mudancas |= {"usuario": identidade.usuario, "dispositivo": identidade.dispositivo}
        self._atualizar(**mudancas)

    def _atualizar(self, **mudancas) -> None:
        self._snapshot = self._snapshot.com(**mudancas)
        self._notificar(self._snapshot)
