"""Regras de negócio, sem nada de Discord: o bot e os testes chamam esta camada."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from livekit import api

from app import protocolo as proto
from app.config import Config
from app.db.repositorio import Dispositivo, Repositorio, Sessao
from app.services.gateway import ClienteOffline, ConexaoPerdida, Gateway, SemResposta
from app.services.livekit_admin import AdminLiveKit
from app.services.tokens import EmissorTokens

log = logging.getLogger(__name__)

RESOLUCOES = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}
CODECS = ("vp8", "h264")
NOME_SALA = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
CHAVE_ESPECTADOR = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
ALFABETO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O e 1/I


class ErroTransmissao(Exception):
    """Erro esperado, com mensagem pronta para mostrar ao usuário."""


@dataclass(frozen=True)
class Usuario:
    discord_id: int
    nome: str


@dataclass(frozen=True)
class PedidoTransmissao:
    sala: str
    resolucao: str | None = None
    fps: int | None = None
    bitrate_kbps: int | None = None
    codec: str | None = None


@dataclass(frozen=True)
class AcessoEspectador:
    livekit_url: str
    token: str
    sala: str
    transmissor: str


@dataclass(frozen=True)
class StatusUsuario:
    dispositivo: Dispositivo | None
    online: bool
    sessao: Sessao | None
    espectadores: int = 0


@dataclass(frozen=True)
class ResultadoParada:
    sessao: Sessao
    confirmado: bool               # o PC respondeu que parou
    derrubado_no_servidor: bool    # o PC não respondeu, mas o backend o tirou da sala pelo LiveKit


# Saídas do LiveKit que não significam "a transmissão acabou".
_SAIDAS_IGNORADAS = {
    api.DisconnectReason.DUPLICATE_IDENTITY,  # o próprio PC reconectou e substituiu a conexão antiga
    api.DisconnectReason.MIGRATION,
}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _definidos(pedido: PedidoTransmissao) -> list[str]:
    """Os parâmetros que a pessoa digitou no /transmitir. Os outros, o PC completa com os padrões dele."""
    campos = {"resolucao": pedido.resolucao, "fps": pedido.fps, "bitrate_kbps": pedido.bitrate_kbps, "codec": pedido.codec}
    return [nome for nome, valor in campos.items() if valor is not None]


def validar_sala(nome: str) -> str:
    nome = nome.strip().lower()
    if not NOME_SALA.match(nome):
        raise ErroTransmissao(
            "Nome de sala inválido: use até 32 caracteres entre letras minúsculas, números, `-` e `_`."
        )
    return nome


class ServicoTransmissao:
    def __init__(self, repo: Repositorio, gateway: Gateway, tokens: EmissorTokens, config: Config,
                 admin: AdminLiveKit | None = None) -> None:
        self.repo = repo
        self.gateway = gateway
        self.tokens = tokens
        self.config = config
        self.admin = admin or AdminLiveKit.de_config(config)
        # Espectadores por sala, alimentado pelos webhooks do LiveKit (some se o backend reiniciar).
        self._espectadores: defaultdict[str, set[str]] = defaultdict(set)
        # Impede dois /transmitir simultâneos do mesmo usuário passarem pela checagem juntos.
        self._travas: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # --- pareamento -----------------------------------------------------------

    async def gerar_codigo(self, usuario: Usuario) -> str:
        await self.repo.salvar_usuario(usuario.discord_id, usuario.nome)
        codigo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(6))
        await self.repo.criar_codigo(usuario.discord_id, codigo, time.time() + self.config.validade_codigo_s)
        return f"{codigo[:3]}-{codigo[3:]}"

    async def parear(self, codigo: str, nome_dispositivo: str) -> tuple[Dispositivo, str] | None:
        normalizado = codigo.replace("-", "").replace(" ", "").upper()
        discord_id = await self.repo.consumir_codigo(normalizado, time.time())
        if discord_id is None:
            return None
        token = secrets.token_urlsafe(32)
        await self.repo.salvar_dispositivo(discord_id, nome_dispositivo.strip(), hash_token(token))
        dispositivo = await self.repo.dispositivo_do_usuario(discord_id)
        assert dispositivo is not None
        log.info("PC '%s' pareado ao usuário %s", dispositivo.nome, discord_id)
        return dispositivo, token

    async def autenticar(self, device_token: str) -> Dispositivo | None:
        dispositivo = await self.repo.dispositivo_por_token(hash_token(device_token))
        if dispositivo is not None:
            await self.repo.marcar_visto(dispositivo.id)
        return dispositivo

    # --- eventos da conexão ---------------------------------------------------

    async def ao_conectar(self, dispositivo: Dispositivo, sessao_do_cliente: str | None) -> None:
        """O cliente é quem sabe o que está transmitindo: alinha o banco com ele."""
        encerradas = await self.repo.encerrar_sessoes_do_usuario(
            dispositivo.discord_id, "cliente reconectou sem esta transmissão", exceto=sessao_do_cliente
        )
        if encerradas:
            log.info("%d sessão(ões) de %s encerrada(s) na reconexão", encerradas, dispositivo.discord_id)
        if sessao_do_cliente:
            await self.repo.atualizar_sessao(sessao_do_cliente, "ativa", discord_id=dispositivo.discord_id)

    async def ao_atualizar_transmissao(self, dispositivo: Dispositivo, evento: proto.TransmissaoAtualizada) -> None:
        sessao = await self.repo.sessao(evento.sessao_id)
        if sessao is None or sessao.discord_id != dispositivo.discord_id or sessao.estado != "ativa":
            return
        await self._gravar_parametros(evento.sessao_id, evento.video)
        log.info("transmissão %s atualizada pelo PC: %dx%d@%d", evento.sessao_id,
                 evento.video.largura, evento.video.altura, evento.video.fps)

    async def _gravar_parametros(self, sessao_id: str, video: proto.ParametrosVideo) -> None:
        await self.repo.atualizar_parametros_sessao(
            sessao_id, f"{video.largura}x{video.altura}", video.fps, video.bitrate_kbps, video.codec)

    async def ao_encerrar_transmissao(self, dispositivo: Dispositivo, evento: proto.TransmissaoEncerrada) -> None:
        motivo = evento.motivo or "encerrada pelo cliente"
        await self.repo.atualizar_sessao(evento.sessao_id, "encerrada", motivo, discord_id=dispositivo.discord_id)
        log.info("transmissão %s encerrada pelo cliente: %s", evento.sessao_id, motivo)

    # --- transmissão ----------------------------------------------------------

    async def verificar_pode_transmitir(self, usuario: Usuario) -> Dispositivo:
        """Checagens rápidas, para o bot responder na hora antes do defer()."""
        dispositivo = await self.repo.dispositivo_do_usuario(usuario.discord_id)
        if dispositivo is None:
            raise ErroTransmissao("Você ainda não pareou um PC. Use `/parear` e digite o código no cliente desktop.")
        if not self.gateway.online(usuario.discord_id):
            raise ErroTransmissao(f"Seu PC (**{dispositivo.nome}**) está offline. Abra o cliente desktop.")
        sessao = await self.repo.sessao_em_andamento(usuario.discord_id)
        if sessao is not None:
            raise ErroTransmissao(f"Você já está transmitindo na sala `{sessao.sala}`. Use `/parar` antes.")
        return dispositivo

    async def transmitir(self, usuario: Usuario, pedido: PedidoTransmissao) -> Sessao:
        nome_sala = validar_sala(pedido.sala)
        async with self._travas[usuario.discord_id]:
            await self.verificar_pode_transmitir(usuario)
            await self.repo.salvar_usuario(usuario.discord_id, usuario.nome)
            sala = await self.repo.obter_ou_criar_sala(nome_sala, usuario.discord_id)

            resolucao = pedido.resolucao or sala.resolucao
            codec = pedido.codec or sala.codec
            if resolucao not in RESOLUCOES or codec not in CODECS:
                raise ErroTransmissao("Resolução ou codec não suportado.")
            fps = pedido.fps or sala.fps
            bitrate = pedido.bitrate_kbps or sala.bitrate_kbps
            largura, altura = RESOLUCOES[resolucao]

            sessao_id = uuid.uuid4().hex
            # 128 bits: impossível de adivinhar. Fica em texto puro no banco de propósito: o bot
            # posta o link no canal (o Discord guarda em claro de qualquer jeito) e o /assistir
            # precisa mostrá-lo de novo. Só vale enquanto a sessão estiver ativa.
            chave = secrets.token_urlsafe(16)
            await self.repo.criar_sessao(sessao_id, usuario.discord_id, nome_sala, resolucao, fps, bitrate, codec, chave)
            comando = proto.Iniciar(
                id=sessao_id,
                sala=nome_sala,
                livekit_url=self.tokens.livekit_url,
                token=self.tokens.publicador(usuario.discord_id, usuario.nome, nome_sala),
                video=proto.ParametrosVideo(largura=largura, altura=altura, fps=fps,
                                            bitrate_kbps=bitrate, codec=codec),
                definidos=_definidos(pedido),
            )
            resultado = await self._executar(usuario.discord_id, comando, self.config.timeout_inicio_s, sessao_id)
            if not resultado.ok:
                await self.repo.atualizar_sessao(sessao_id, "falhou", resultado.detalhe)
                raise ErroTransmissao(f"O cliente desktop não conseguiu iniciar: {resultado.detalhe or 'erro desconhecido'}")

            await self.repo.atualizar_sessao(sessao_id, "ativa")
            if resultado.video is not None:  # o PC pode ter completado com os padrões dele
                await self._gravar_parametros(sessao_id, resultado.video)
            sessao = await self.repo.sessao(sessao_id)
            assert sessao is not None
            log.info("transmissão %s ativa: %s na sala %s (%s@%d)",
                     sessao_id, usuario.nome, nome_sala, sessao.resolucao, sessao.fps)
            return sessao

    async def parar(self, usuario: Usuario) -> ResultadoParada:
        async with self._travas[usuario.discord_id]:
            sessao = await self.repo.sessao_em_andamento(usuario.discord_id)
            if sessao is None:
                raise ErroTransmissao("Você não está transmitindo agora.")
            confirmado = derrubado = False
            try:
                comando = proto.Parar(id=uuid.uuid4().hex, sessao_id=sessao.id)
                resultado = await self.gateway.comando(usuario.discord_id, comando,
                                                       self.config.timeout_ack_s, self.config.timeout_ack_s)
                confirmado = resultado.ok
            except (ClienteOffline, ConexaoPerdida, SemResposta) as erro:
                log.warning("parada de %s sem confirmação do PC: %s", sessao.id, erro)
            if not confirmado:
                # O PC pode ter perdido só o WebSocket e continuar transmitindo: tira pelo servidor.
                derrubado = await self.admin.remover_participante(sessao.sala, f"pc-{usuario.discord_id}")
            detalhe = ("parada pelo /parar" if confirmado
                       else "parada sem confirmação do PC; removido do LiveKit" if derrubado
                       else "parada sem confirmação do PC")
            await self.repo.atualizar_sessao(sessao.id, "encerrada", detalhe)
            return ResultadoParada(sessao, confirmado, derrubado)

    async def _executar(self, discord_id: int, comando: proto.Iniciar, timeout_resultado: float,
                        sessao_id: str) -> proto.Resultado:
        try:
            return await self.gateway.comando(discord_id, comando, self.config.timeout_ack_s, timeout_resultado)
        except (ClienteOffline, ConexaoPerdida):
            await self.repo.atualizar_sessao(sessao_id, "falhou", "conexão com o PC caiu")
            raise ErroTransmissao("A conexão com o seu PC caiu durante o pedido. Tente de novo.") from None
        except SemResposta as erro:
            await self.repo.atualizar_sessao(sessao_id, "falhou", f"sem resposta ({erro.fase})")
            if erro.fase == "resultado":
                # O PC confirmou mas não terminou a tempo: pede para parar, para não
                # sobrar uma transmissão órfã que ninguém controla.
                self._parar_sem_esperar(discord_id, sessao_id)
                raise ErroTransmissao("Seu PC recebeu o pedido, mas não conseguiu iniciar a tempo.") from None
            raise ErroTransmissao("Seu PC não respondeu. O cliente desktop está travado?") from None

    def _parar_sem_esperar(self, discord_id: int, sessao_id: str) -> None:
        try:
            self.gateway.conexao(discord_id).enviar(proto.Parar(id=uuid.uuid4().hex, sessao_id=sessao_id))
        except ClienteOffline:
            pass

    # --- webhooks do LiveKit ---------------------------------------------------

    async def ao_evento_livekit(self, evento: api.WebhookEvent) -> None:
        sala, participante = evento.room.name, evento.participant
        if evento.event == "room_finished":
            self._espectadores.pop(sala, None)
            return
        identidade = participante.identity
        if identidade.startswith("espectador-"):
            if evento.event == "participant_joined":
                self._espectadores[sala].add(identidade)
            elif evento.event == "participant_left":
                self._espectadores[sala].discard(identidade)
        elif identidade.startswith("pc-") and evento.event == "participant_left":
            await self._pc_saiu_do_livekit(sala, participante)

    async def _pc_saiu_do_livekit(self, sala: str, participante) -> None:
        """Cobre o PC que caiu do LiveKit sem conseguir avisar o backend (ex.: sem internet)."""
        if participante.disconnect_reason in _SAIDAS_IGNORADAS:
            return
        try:
            discord_id = int(participante.identity.removeprefix("pc-"))
        except ValueError:
            return
        sessao = await self.repo.sessao_em_andamento(discord_id)
        if sessao is None or sessao.sala != sala or sessao.estado != "ativa":
            return
        # Uma conexão que entrou ANTES desta sessão começar é de uma transmissão anterior
        # (evento atrasado): não pode encerrar a atual.
        inicio = datetime.fromisoformat(sessao.iniciada_em).replace(tzinfo=UTC).timestamp()
        if participante.joined_at < inicio:
            return
        await self.repo.atualizar_sessao(sessao.id, "encerrada", "o PC saiu do LiveKit")
        log.info("transmissão %s encerrada: o PC saiu do LiveKit", sessao.id)

    def espectadores(self, sala: str) -> int:
        return len(self._espectadores.get(sala, ()))

    # --- consultas ------------------------------------------------------------

    async def status(self, usuario: Usuario) -> StatusUsuario:
        sessao = await self.repo.sessao_em_andamento(usuario.discord_id)
        return StatusUsuario(
            dispositivo=await self.repo.dispositivo_do_usuario(usuario.discord_id),
            online=self.gateway.online(usuario.discord_id),
            sessao=sessao,
            espectadores=self.espectadores(sessao.sala) if sessao else 0,
        )

    def link_espectador(self, sessao: Sessao) -> str:
        return f"{self.config.url_publica.rstrip('/')}/assistir/{sessao.chave_espectador}"

    async def links_da_sala(self, sala: str) -> list[tuple[str, str]]:
        """(quem transmite, link) de cada transmissão ativa na sala."""
        return [(nome, self.link_espectador(sessao))
                for sessao, nome in await self.repo.sessoes_ativas_na_sala(validar_sala(sala))]

    async def descrever_link(self, chave: str) -> tuple[str, str] | None:
        """(sala, quem transmite) sem emitir token: é o que o preview de link do Discord recebe."""
        if not CHAVE_ESPECTADOR.match(chave):
            return None
        achado = await self.repo.sessao_ativa_por_chave(chave)
        return (achado[0].sala, achado[1]) if achado else None

    async def acesso_por_chave(self, chave: str) -> AcessoEspectador | None:
        descricao = await self.descrever_link(chave)
        if descricao is None:
            return None
        sala, transmissor = descricao
        return AcessoEspectador(self.tokens.livekit_url, self.tokens.espectador(sala), sala, transmissor)

    async def sugerir_salas(self, prefixo: str) -> list[str]:
        return await self.repo.listar_salas(prefixo.strip().lower())
