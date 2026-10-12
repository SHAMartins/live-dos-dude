"""Webhooks do LiveKit: assinatura obrigatória, contagem de espectadores e queda do PC."""

from __future__ import annotations

import base64
import hashlib
import time

from google.protobuf.json_format import MessageToJson
from livekit import api

from app.services.transmissao import PedidoTransmissao
from auxiliares import FULANO


def _assinar(ambiente, corpo: str) -> str:
    """Igual ao LiveKit: JWT assinado com o API secret, com o SHA-256 do corpo."""
    resumo = base64.b64encode(hashlib.sha256(corpo.encode()).digest()).decode()
    return (api.AccessToken(ambiente.config.livekit_api_key, ambiente.config.livekit_api_secret.get_secret_value())
            .with_sha256(resumo).to_jwt())


def _evento(tipo: str, sala: str, identidade: str = "", **participante) -> str:
    evento = api.WebhookEvent(event=tipo, room=api.Room(name=sala), id="EV_teste", created_at=int(time.time()))
    if identidade:
        evento.participant.CopyFrom(api.ParticipantInfo(identity=identidade, **participante))
    return MessageToJson(evento)


async def _enviar(ambiente, corpo: str, autorizacao: str | None = "assinar") -> int:
    cabecalhos = {"Content-Type": "application/webhook+json"}
    if autorizacao == "assinar":
        cabecalhos["Authorization"] = _assinar(ambiente, corpo)
    elif autorizacao:
        cabecalhos["Authorization"] = autorizacao
    resposta = await ambiente.app.test_client().post("/livekit/webhook", data=corpo, headers=cabecalhos)
    return resposta.status_code


async def test_conta_espectadores_entrando_e_saindo(ambiente):
    for i in range(3):
        assert await _enviar(ambiente, _evento("participant_joined", "dev", f"espectador-{i}")) == 200
    await _enviar(ambiente, _evento("participant_left", "dev", "espectador-1"))
    await _enviar(ambiente, _evento("participant_joined", "outra", "espectador-9"))
    assert ambiente.servico.espectadores("dev") == 2
    assert ambiente.servico.espectadores("outra") == 1

    await _enviar(ambiente, _evento("room_finished", "dev"))
    assert ambiente.servico.espectadores("dev") == 0


async def test_recusa_sem_assinatura_com_assinatura_errada_ou_corpo_adulterado(ambiente):
    corpo = _evento("participant_joined", "dev", "espectador-1")
    assert await _enviar(ambiente, corpo, autorizacao=None) == 401
    assert await _enviar(ambiente, corpo, autorizacao="nao.e.jwt") == 401

    outro_segredo = api.AccessToken(ambiente.config.livekit_api_key, "x" * 40).with_sha256("abc").to_jwt()
    assert await _enviar(ambiente, corpo, autorizacao=outro_segredo) == 401

    assinatura_de_outro_corpo = _assinar(ambiente, _evento("participant_joined", "dev", "espectador-2"))
    assert await _enviar(ambiente, corpo, autorizacao=assinatura_de_outro_corpo) == 401
    assert ambiente.servico.espectadores("dev") == 0


async def _transmitindo(ambiente, cliente_pareado):
    await cliente_pareado()
    return await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))


async def test_pc_que_sai_do_livekit_encerra_a_sessao(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    corpo = _evento("participant_left", "dev", "pc-111", joined_at=int(time.time()),
                    disconnect_reason=api.DisconnectReason.CONNECTION_TIMEOUT)
    assert await _enviar(ambiente, corpo) == 200
    atual = await ambiente.repo.sessao(sessao.id)
    assert (atual.estado, atual.detalhe) == ("encerrada", "o PC saiu do LiveKit")


async def test_reconexao_do_pc_nao_encerra_a_sessao(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    corpo = _evento("participant_left", "dev", "pc-111", joined_at=int(time.time()),
                    disconnect_reason=api.DisconnectReason.DUPLICATE_IDENTITY)
    await _enviar(ambiente, corpo)
    assert (await ambiente.repo.sessao(sessao.id)).estado == "ativa"


async def test_evento_atrasado_de_conexao_anterior_nao_encerra_a_sessao_nova(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    corpo = _evento("participant_left", "dev", "pc-111", joined_at=int(time.time()) - 3600)
    await _enviar(ambiente, corpo)
    assert (await ambiente.repo.sessao(sessao.id)).estado == "ativa"


async def test_saida_do_pc_em_outra_sala_nao_afeta(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    await _enviar(ambiente, _evento("participant_left", "outra", "pc-111", joined_at=int(time.time())))
    assert (await ambiente.repo.sessao(sessao.id)).estado == "ativa"


async def test_status_mostra_quantos_assistem(ambiente, cliente_pareado):
    await _transmitindo(ambiente, cliente_pareado)
    await _enviar(ambiente, _evento("participant_joined", "dev", "espectador-a"))
    assert (await ambiente.servico.status(FULANO)).espectadores == 1
