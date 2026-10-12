from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from hypercorn.config import Config as ConfigHypercorn
from quart import Quart

from app import protocolo as proto
from app.api.espectador import registrar_espectador
from app.api.webhook import registrar_webhook
from app.api.ws_cliente import registrar_ws
from app.config import Config
from app.services.gateway import Gateway
from app.services.transmissao import ServicoTransmissao

PASTA_WEB = Path(__file__).resolve().parent.parent / "web"


def criar_app(servico: ServicoTransmissao, gateway: Gateway, estado_bot: Callable[[], str]) -> Quart:
    app = Quart(__name__, template_folder=str(PASTA_WEB), static_folder=str(PASTA_WEB / "static"))

    @app.get("/saude")
    async def saude() -> dict:
        return {"ok": True, "clientes_online": gateway.total_online, "discord": estado_bot()}

    registrar_ws(app, servico, gateway)
    registrar_espectador(app, servico)
    registrar_webhook(app, servico)
    return app


def gatilho_desligamento(parar: asyncio.Event, gateway: Gateway) -> Callable[[], Awaitable[None]]:
    """shutdown_trigger do Hypercorn que derruba os clientes conectados.

    Sem isso o desligamento trava: desde o Python 3.12 o servidor espera TODAS as conexões
    fecharem, e um cliente WebSocket pingando nunca fecha sozinho.
    """
    async def esperar_e_fechar() -> None:
        await parar.wait()
        gateway.fechar_todas(proto.Fechamento.REINICIANDO, "reiniciando")

    return esperar_e_fechar


def config_hypercorn(config: Config) -> ConfigHypercorn:
    hc = ConfigHypercorn()
    hc.bind = [f"{config.host}:{config.porta}"]
    # Ping do servidor: detecta clientes mortos e mantém o mapeamento de NAT aberto.
    hc.websocket_ping_interval = 20.0
    # As mensagens do protocolo são pequenas; 64 KiB corta abuso sem atrapalhar.
    hc.websocket_max_message_size = 64 * 1024
    hc.graceful_timeout = 3.0
    # Logger pronto: o Hypercorn não cria handler próprio e não duplica as linhas do log.
    hc.errorlog = logging.getLogger("hypercorn.error")
    return hc
