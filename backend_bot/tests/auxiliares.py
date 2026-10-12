from __future__ import annotations

import asyncio

from app.services.transmissao import Usuario

FULANO = Usuario(discord_id=111, nome="Fulano")
CICLANO = Usuario(discord_id=222, nome="Ciclano")


async def esperar_online(gateway, usuario: Usuario, online: bool = True) -> None:
    async with asyncio.timeout(2):
        while gateway.online(usuario.discord_id) != online:
            await asyncio.sleep(0.01)
