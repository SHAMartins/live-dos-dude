"""Endpoint WebSocket dos clientes desktop (/ws/cliente). Contrato: docs/protocolo.md."""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError
from quart import Quart, websocket
from quart.wrappers import Websocket

from app import protocolo as proto
from app.services.gateway import ConexaoCliente, Gateway
from app.services.transmissao import ServicoTransmissao

log = logging.getLogger(__name__)

TIMEOUT_HANDSHAKE_S = 10.0
ATRASO_CODIGO_INVALIDO_S = 1.0  # freia tentativa e erro de códigos de pareamento


def registrar_ws(app: Quart, servico: ServicoTransmissao, gateway: Gateway) -> None:
    @app.websocket("/ws/cliente")
    async def ws_cliente() -> None:
        ws: Websocket = websocket._get_current_object()
        conexao = await _handshake(ws, servico)
        if conexao is None:
            return

        gateway.registrar(conexao)
        recepcao = asyncio.create_task(_receber(ws, conexao, servico))
        envio = asyncio.create_task(_enviar(ws, conexao))
        try:
            # Termina quando o cliente cai (recepção) ou quando mandamos fechar (envio).
            await asyncio.wait({recepcao, envio}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for tarefa in (recepcao, envio):
                tarefa.cancel()
            await asyncio.gather(recepcao, envio, return_exceptions=True)
            gateway.remover(conexao)
            log.info("PC '%s' (%s) desconectou", conexao.dispositivo.nome, conexao.discord_id)


async def _handshake(ws: Websocket, servico: ServicoTransmissao) -> ConexaoCliente | None:
    try:
        bruto = await asyncio.wait_for(ws.receive(), TIMEOUT_HANDSHAKE_S)
        mensagem = proto.HANDSHAKE.validate_json(bruto)
    except TimeoutError:
        await _recusar(ws, proto.Fechamento.NAO_AUTENTICADO, "handshake_timeout",
                       "Nenhuma mensagem de autenticação recebida a tempo.")
        return None
    except ValidationError:
        await _recusar(ws, proto.Fechamento.PROTOCOLO_INVALIDO, "handshake_invalido",
                       "A primeira mensagem deve ser 'hello' ou 'parear'.")
        return None

    if mensagem.v != proto.VERSAO:
        await _recusar(ws, proto.Fechamento.VERSAO_INCOMPATIVEL, "versao_incompativel",
                       f"Este backend fala o protocolo v{proto.VERSAO}. Atualize o cliente desktop.")
        return None

    if isinstance(mensagem, proto.Parear):
        pareamento = await servico.parear(mensagem.codigo, mensagem.nome_dispositivo)
        if pareamento is None:
            await asyncio.sleep(ATRASO_CODIGO_INVALIDO_S)
            await _recusar(ws, proto.Fechamento.NAO_AUTENTICADO, "codigo_invalido",
                           "Código inválido ou expirado. Gere outro com /parear no Discord.")
            return None
        dispositivo, token = pareamento
        boas_vindas: proto.Pareado | proto.BemVindo = proto.Pareado(
            device_token=token, usuario=dispositivo.usuario_nome, dispositivo=dispositivo.nome)
        sessao_do_cliente = None
    else:
        dispositivo = await servico.autenticar(mensagem.device_token)
        if dispositivo is None:
            await _recusar(ws, proto.Fechamento.NAO_AUTENTICADO, "token_invalido",
                           "PC não reconhecido. Pareie de novo com /parear no Discord.")
            return None
        boas_vindas = proto.BemVindo(usuario=dispositivo.usuario_nome, dispositivo=dispositivo.nome)
        sessao_do_cliente = mensagem.sessao_ativa

    await servico.ao_conectar(dispositivo, sessao_do_cliente)
    conexao = ConexaoCliente(dispositivo)
    conexao.enviar(boas_vindas)
    log.info("PC '%s' de %s conectado (app %s)", dispositivo.nome, dispositivo.usuario_nome,
             getattr(mensagem, "versao_cliente", "?"))
    return conexao


def _fmt(valor, formato: str = ".1f") -> str:
    return "?" if valor is None else format(valor, formato)


def registrar_telemetria(nome: str, m: proto.Telemetria) -> None:
    """Uma linha por amostra, pensada para ler em `docker compose logs | grep telemetria`."""
    log.info(
        "telemetria '%s' app %s sessão %s | captura %s %s/%s fps (%s ms/quadro) | enviado %s fps, encode %s ms (%s) %sx%s"
        " | limite=%s | CPU app %s%% sistema %s%% GPU %s%% (%s threads)",
        nome, m.versao, (m.sessao_id or "?")[:8], m.captura or "?", _fmt(m.captura_fps), _fmt(m.alvo_fps, "d"),
        _fmt(m.captura_ms), _fmt(m.enviado_fps), _fmt(m.encode_ms), m.codificador or "?",
        _fmt(m.largura, "d"), _fmt(m.altura, "d"), m.limite or "?", _fmt(m.cpu_app_pct, ".0f"),
        _fmt(m.cpu_sistema_pct, ".0f"), _fmt(m.gpu_pct, ".0f"), _fmt(m.nucleos, "d"))


async def _receber(ws: Websocket, conexao: ConexaoCliente, servico: ServicoTransmissao) -> None:
    while True:
        bruto = await ws.receive()
        try:
            mensagem = proto.DO_CLIENTE.validate_json(bruto)
        except ValidationError as erro:
            log.warning("mensagem inválida de %s: %s", conexao.discord_id, erro.errors()[:1])
            conexao.enviar(proto.Erro(codigo="mensagem_invalida", mensagem="Mensagem fora do protocolo."))
            continue
        if isinstance(mensagem, proto.TransmissaoEncerrada):
            await servico.ao_encerrar_transmissao(conexao.dispositivo, mensagem)
        elif isinstance(mensagem, proto.TransmissaoAtualizada):
            await servico.ao_atualizar_transmissao(conexao.dispositivo, mensagem)
        elif isinstance(mensagem, proto.Telemetria):
            registrar_telemetria(conexao.dispositivo.nome, mensagem)
        else:
            conexao.receber(mensagem)


async def _enviar(ws: Websocket, conexao: ConexaoCliente) -> None:
    while True:
        item = await conexao.proxima_saida()
        if isinstance(item, tuple):
            codigo, motivo = item
            await ws.send(proto.Erro(codigo=motivo, mensagem="Conexão encerrada pelo servidor.").model_dump_json())
            await ws.close(codigo, motivo)
            return
        await ws.send(item)


async def _recusar(ws: Websocket, codigo: int, erro: str, mensagem: str) -> None:
    await ws.send(proto.Erro(codigo=erro, mensagem=mensagem).model_dump_json())
    # O "reason" do fechamento tem limite de 123 bytes: vai só o código, em ASCII.
    await ws.close(codigo, erro)
