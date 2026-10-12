"""Slash commands. Só traduzem Discord <-> ServicoTransmissao; a regra fica no serviço."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import discord
from discord import app_commands

from app.config import Config
from app.services.transmissao import ErroTransmissao, PedidoTransmissao, ServicoTransmissao, Usuario


def tem_permissao(membro: discord.abc.User, cargo: str | None) -> bool:
    """Sem cargo configurado, qualquer membro pode; senão, precisa do cargo (por nome ou ID)."""
    if not cargo:
        return True
    return any(str(r.id) == cargo or r.name == cargo for r in getattr(membro, "roles", []))


def _usuario(interaction: discord.Interaction) -> Usuario:
    return Usuario(discord_id=interaction.user.id, nome=interaction.user.display_name)


def _timestamp_discord(utc_sqlite: str) -> str:
    epoch = int(datetime.fromisoformat(utc_sqlite).replace(tzinfo=UTC).timestamp())
    return f"<t:{epoch}:R>"


def criar_comandos(servico: ServicoTransmissao, config: Config) -> list[app_commands.Command]:

    async def autocompletar_sala(_interaction: discord.Interaction, atual: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=nome, value=nome) for nome in await servico.sugerir_salas(atual)]

    @app_commands.command(name="parear", description="Gera um código para vincular o seu PC (cliente desktop)")
    async def parear(interaction: discord.Interaction) -> None:
        codigo = await servico.gerar_codigo(_usuario(interaction))
        minutos = config.validade_codigo_s // 60
        await interaction.response.send_message(
            f"Seu código de pareamento: **`{codigo}`**\n"
            f"Digite no cliente desktop. Vale por {minutos} min e só uma vez.",
            ephemeral=True,
        )

    @app_commands.command(name="transmitir", description="Liga a transmissão da tela do seu PC")
    @app_commands.describe(
        sala="Nome da sala (letras minúsculas, números, - e _)",
        resolucao="Padrão: o da sala (720p)",
        fps="Quadros por segundo. Padrão: o da sala (30)",
        bitrate="Bitrate máximo em kbps. Padrão: o da sala (2500)",
        codec="VP8 é o padrão; H.264 gasta menos CPU de quem assiste",
    )
    @app_commands.autocomplete(sala=autocompletar_sala)
    @app_commands.guild_only()
    async def transmitir(
        interaction: discord.Interaction,
        sala: str,
        resolucao: Literal["480p", "720p", "1080p"] | None = None,
        fps: Literal[15, 30, 60] | None = None,
        bitrate: app_commands.Range[int, 300, 8000] | None = None,
        codec: Literal["vp8", "h264"] | None = None,
    ) -> None:
        usuario = _usuario(interaction)
        if not tem_permissao(interaction.user, config.cargo_transmissor):
            await interaction.response.send_message("Você não tem o cargo necessário para transmitir.", ephemeral=True)
            return
        # Erros rápidos (sem PC, PC offline, já transmitindo) saem na hora e só para quem pediu.
        try:
            await servico.verificar_pode_transmitir(usuario)
        except ErroTransmissao as erro:
            await interaction.response.send_message(f"❌ {erro}", ephemeral=True)
            return

        # Daqui em diante pode passar dos 3 s que o Discord dá para responder.
        await interaction.response.defer(thinking=True)
        try:
            sessao = await servico.transmitir(usuario, PedidoTransmissao(sala, resolucao, fps, bitrate, codec))
        except ErroTransmissao as erro:
            await interaction.followup.send(f"❌ {erro}")
            return
        await interaction.followup.send(
            f"🔴 **{usuario.nome}** está transmitindo na sala `{sessao.sala}` "
            f"({sessao.resolucao} · {sessao.fps} fps · {sessao.bitrate_kbps} kbps · {sessao.codec.upper()}).\n"
            f"**Assistir:** {servico.link_espectador(sessao)}"
        )

    @app_commands.command(name="parar", description="Desliga a sua transmissão")
    async def parar(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            parada = await servico.parar(_usuario(interaction))
        except ErroTransmissao as erro:
            await interaction.followup.send(f"❌ {erro}")
            return
        if parada.confirmado:
            aviso = ""
        elif parada.derrubado_no_servidor:
            aviso = "\nO PC não respondeu, então a transmissão foi derrubada pelo servidor."
        else:
            aviso = "\n⚠️ O PC não confirmou. Se ainda estiver no ar, feche pelo cliente desktop."
        await interaction.followup.send(f"⏹️ Transmissão na sala `{parada.sessao.sala}` encerrada.{aviso}")

    @app_commands.command(name="status", description="Mostra se o seu PC está conectado e o que está transmitindo")
    async def status(interaction: discord.Interaction) -> None:
        estado = await servico.status(_usuario(interaction))
        if estado.dispositivo is None:
            texto = "Nenhum PC pareado. Use `/parear`."
        else:
            conexao = "🟢 online" if estado.online else "⚫ offline"
            texto = f"PC: **{estado.dispositivo.nome}** ({conexao})"
            if estado.sessao:
                s = estado.sessao
                texto += (f"\nTransmitindo na sala `{s.sala}` desde {_timestamp_discord(s.iniciada_em)} "
                          f"({s.resolucao} · {s.fps} fps) · 👀 {estado.espectadores} assistindo")
            else:
                texto += "\nSem transmissão no momento."
        await interaction.response.send_message(texto, ephemeral=True)

    @app_commands.command(name="assistir", description="Mostra o link para assistir às transmissões de uma sala")
    @app_commands.describe(sala="Nome da sala")
    @app_commands.autocomplete(sala=autocompletar_sala)
    async def assistir(interaction: discord.Interaction, sala: str) -> None:
        try:
            links = await servico.links_da_sala(sala)
        except ErroTransmissao as erro:
            await interaction.response.send_message(f"❌ {erro}", ephemeral=True)
            return
        if not links:
            await interaction.response.send_message(f"Ninguém está transmitindo na sala `{sala}` agora.", ephemeral=True)
            return
        await interaction.response.send_message(
            "\n".join(f"🔴 **{nome}**: {link}" for nome, link in links), ephemeral=True)

    return [parear, transmitir, parar, status, assistir]
