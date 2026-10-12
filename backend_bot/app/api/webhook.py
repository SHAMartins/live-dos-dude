"""POST /livekit/webhook: eventos do LiveKit (participante entrou/saiu, sala encerrada).

O endpoint é público (o LiveKit está em outra VM), então TODA requisição tem a assinatura
conferida: um JWT assinado com o API secret, contendo o SHA-256 do corpo.
"""

from __future__ import annotations

import logging

from quart import Quart, request

from app.services.transmissao import ServicoTransmissao

log = logging.getLogger(__name__)

TAMANHO_MAXIMO = 64 * 1024


def registrar_webhook(app: Quart, servico: ServicoTransmissao) -> None:
    @app.post("/livekit/webhook")
    async def webhook() -> tuple[str, int]:
        if (request.content_length or 0) > TAMANHO_MAXIMO:
            return "", 413
        corpo = (await request.get_data()).decode("utf-8", errors="replace")
        try:
            evento = servico.admin.receptor_webhook.receive(corpo, request.headers.get("Authorization", ""))
        except Exception as erro:  # noqa: BLE001 - assinatura ausente, inválida ou corpo adulterado
            log.warning("webhook recusado: %s", erro)
            return "", 401
        log.info("webhook: %s %s (sala %s)", evento.event, evento.participant.identity or "-", evento.room.name or "-")
        await servico.ao_evento_livekit(evento)
        return "", 200
