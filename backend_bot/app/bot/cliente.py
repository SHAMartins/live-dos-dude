from __future__ import annotations

import logging

import discord
from discord import app_commands

from app.bot.comandos import criar_comandos
from app.bot.guia import garantir_canal
from app.config import Config
from app.services.transmissao import ServicoTransmissao

log = logging.getLogger(__name__)

# View Channels + Send Messages + Embed Links (preview do link de assistir)
# + Manage Channels (criar o #live-dos-dude) + Read Message History (achar e atualizar o guia).
PERMISSOES_CONVITE = 1024 | 2048 | 16384 | 16 | 65536


class ErroDeConfiguracao(Exception):
    """Erro de setup do bot com mensagem pronta para o log (sem traceback)."""


def link_de_convite(application_id: int) -> str:
    return (f"https://discord.com/oauth2/authorize?client_id={application_id}"
            f"&scope=bot%20applications.commands&permissions={PERMISSOES_CONVITE}&integration_type=0")


class BotDiscord(discord.Client):
    def __init__(self, servico: ServicoTransmissao, config: Config) -> None:
        # Só slash commands: nenhum intent privilegiado é necessário.
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.tree = app_commands.CommandTree(self)
        for comando in criar_comandos(servico, config):
            self.tree.add_command(comando)
        self.tree.error(self._erro_em_comando)

    async def setup_hook(self) -> None:
        servidores = self.config.servidores_discord
        if servidores:
            registrados = 0
            for servidor_id in servidores:
                servidor = discord.Object(id=servidor_id)
                self.tree.copy_global_to(guild=servidor)
                try:
                    sincronizados = await self.tree.sync(guild=servidor)
                except discord.Forbidden:
                    # Um servidor sem acesso não derruba os outros (ex.: o bot foi removido do de teste).
                    log.error("sem acesso ao servidor %s (DISCORD_GUILD_ID): o bot não está nele ou o ID está "
                              "errado. Convite: %s", servidor_id, link_de_convite(self.application_id))
                    continue
                registrados += 1
                log.info("%d comandos registrados no servidor %s", len(sincronizados), servidor_id)
            if not registrados:
                raise ErroDeConfiguracao(
                    f"O bot não tem acesso a nenhum servidor de DISCORD_GUILD_ID ({self.config.discord_guild_id}). "
                    "Ou ele ainda não foi adicionado, ou o ID é de outro servidor. "
                    f"Adicione-o por este link e ligue o backend de novo: {link_de_convite(self.application_id)}"
                )
        else:
            sincronizados = await self.tree.sync()
            log.info("%d comandos registrados globalmente (podem levar até 1 h para aparecer)", len(sincronizados))

    async def on_ready(self) -> None:
        log.info("bot conectado como %s", self.user)
        for servidor in self.guilds:
            await self._publicar_guia(servidor)

    async def on_guild_join(self, servidor: discord.Guild) -> None:
        log.info("bot adicionado ao servidor %s (%s)", servidor.name, servidor.id)
        await self._publicar_guia(servidor)

    async def _publicar_guia(self, servidor: discord.Guild) -> None:
        # Só onde os comandos estão registrados: em outro servidor o guia prometeria comandos que não existem.
        if self.config.servidores_discord and servidor.id not in self.config.servidores_discord:
            return
        try:
            resultado = await garantir_canal(servidor)
        except discord.HTTPException as erro:
            resultado = f"falhou ({erro})"
        nivel = logging.WARNING if ("sem permissão" in resultado or "falta" in resultado or "falhou" in resultado) else logging.INFO
        log.log(nivel, "guia em %s: %s", servidor.name, resultado)
        if nivel == logging.WARNING and self.application_id:
            log.warning("para dar as permissões que faltam, autorize de novo: %s", link_de_convite(self.application_id))

    def estado(self) -> str:
        return "conectado" if self.is_ready() else "conectando"

    async def _erro_em_comando(self, interaction: discord.Interaction, erro: app_commands.AppCommandError) -> None:
        log.error("erro no comando /%s", interaction.command.name if interaction.command else "?", exc_info=erro)
        mensagem = "Erro interno no backend. Veja os logs."
        if interaction.response.is_done():
            await interaction.followup.send(mensagem, ephemeral=True)
        else:
            await interaction.response.send_message(mensagem, ephemeral=True)
