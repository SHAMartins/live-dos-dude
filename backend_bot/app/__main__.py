"""Sobe o bot do Discord e a API (Quart/Hypercorn) no MESMO event loop.

    cd backend_bot && python -m app
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import discord
from hypercorn.asyncio import serve

from app.api.servidor import config_hypercorn, criar_app, gatilho_desligamento
from app.bot.cliente import BotDiscord, ErroDeConfiguracao
from app.config import Config
from app.db.repositorio import Repositorio
from app.services.gateway import Gateway
from app.services.tokens import EmissorTokens
from app.services.transmissao import ServicoTransmissao

log = logging.getLogger("app")


async def executar(config: Config) -> None:
    repo = await Repositorio.abrir(config.banco)
    gateway = Gateway()
    servico = ServicoTransmissao(repo, gateway, EmissorTokens.de_config(config), config)

    token_discord = config.discord_token.get_secret_value() if config.discord_token else ""
    bot = BotDiscord(servico, config) if token_discord else None
    if bot is None:
        log.warning("DISCORD_TOKEN não definido: subindo só a API (modo de teste com o cliente falso)")

    app = criar_app(servico, gateway, bot.estado if bot else lambda: "desativado")
    parar = asyncio.Event()
    _instalar_sinais(parar)
    log.info("API em http://%s:%d  |  WebSocket: /ws/cliente  |  LiveKit: %s",
             config.host, config.porta, config.livekit_url)
    try:
        # TaskGroup: se o bot falhar (ex.: token inválido), a API cai junto em vez de ficar meio viva.
        async with asyncio.TaskGroup() as grupo:
            grupo.create_task(serve(app, config_hypercorn(config), shutdown_trigger=gatilho_desligamento(parar, gateway)))
            if bot is not None:
                grupo.create_task(_rodar_bot(bot, token_discord, parar))
    finally:
        await repo.fechar()


async def _rodar_bot(bot: BotDiscord, token: str, parar: asyncio.Event) -> None:
    conexao = asyncio.create_task(bot.start(token))
    espera = asyncio.create_task(parar.wait())
    try:
        await asyncio.wait({conexao, espera}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        espera.cancel()
        await bot.close()
        await conexao  # propaga erro de login/conexão


def _instalar_sinais(parar: asyncio.Event) -> None:
    if sys.platform == "win32":
        return  # no Windows o Ctrl+C chega como KeyboardInterrupt e cancela tudo
    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sinal, parar.set)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        _executar_com_erros_legiveis()
    except KeyboardInterrupt:
        log.info("encerrado")


def _executar_com_erros_legiveis() -> None:
    """Erros de configuração viram uma linha de log clara, em vez de um traceback de TaskGroup."""
    try:
        asyncio.run(executar(Config()))
    except* ErroDeConfiguracao as grupo:
        log.error("%s", grupo.exceptions[0])
        sys.exit(1)
    except* discord.LoginFailure:
        log.error("O Discord recusou o DISCORD_TOKEN. Gere outro em Developer Portal → Bot → Reset Token "
                  "e cole no .env.")
        sys.exit(1)


if __name__ == "__main__":
    main()
