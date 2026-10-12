"""Canal #live-dos-dude: explica o bot e os comandos. Criado e mantido atualizado pelo próprio bot.

Roda quando o bot entra no servidor e toda vez que liga: se o canal já existe, só edita a
mensagem do guia (assim ela nunca fica desatualizada quando os comandos mudam).
"""

from __future__ import annotations

import logging

import discord

log = logging.getLogger(__name__)

NOME_CANAL = "live-dos-dude"
TOPICO = "Como usar o bot Live dos Dude: transmitir a tela e assistir pelo navegador. Canal mantido pelo bot."
COR = 0xE5484D

# Mantenha em dia com app/bot/comandos.py: tests/test_bot.py confere se todo comando aparece aqui.
COMANDOS = [
    ("/parear", "Gera um código de 6 caracteres (vale 5 min, uso único) para ligar o app do seu PC à sua conta. "
                "Só você vê a resposta."),
    ("/transmitir sala:<nome> [resolucao] [fps] [bitrate] [codec]",
     "Liga a transmissão da tela do **seu** PC e posta o link **Assistir**. O que você digitar vale só "
     "dessa vez; o resto vem da configuração do app."),
    ("/parar", "Encerra a sua transmissão. O link para de funcionar."),
    ("/status", "Mostra se o seu PC está conectado, o que está transmitindo e quantas pessoas estão assistindo."),
    ("/assistir sala:<nome>", "Mostra de novo os links das transmissões ativas daquela sala."),
]


def montar_guia() -> discord.Embed:
    guia = discord.Embed(
        title="📺 Live dos Dude",
        description=(
            "Transmita a tela (ou uma janela) do seu PC para o servidor, com baixa latência. "
            "Quem assiste só clica no link: **não precisa instalar nada**, funciona até no celular."
        ),
        color=COR,
    )
    guia.add_field(
        name="👀 Quero só assistir",
        value="Clique em **Assistir** na mensagem 🔴 que o bot posta quando alguém começa a transmitir. "
              "Abre no navegador, sem login.",
        inline=False,
    )
    guia.add_field(
        name="🎥 Quero transmitir (só na primeira vez)",
        value="1. Instale o app **Live dos Dude** no seu PC (Windows 10/11). Peça o instalador a quem administra o servidor.\n"
              "2. Aqui no Discord, use `/parear` e digite o código na janela do app.\n"
              "3. Pronto: o app fica na bandeja. Daí em diante é só `/transmitir sala:jogo`.",
        inline=False,
    )
    guia.add_field(name="⌨️ Comandos", value="\n".join(f"**`{c}`**\n{d}" for c, d in COMANDOS), inline=False)
    guia.add_field(
        name="⚙️ Dicas",
        value="• No app você escolhe **o que transmitir** (tela ou janela) e a qualidade padrão.\n"
              "• Mudou algo durante a transmissão? Clique em **Aplicar agora** no app: o link continua o mesmo.\n"
              "• Ninguém consegue ligar a tela de outra pessoa: o `/transmitir` só aciona o PC de quem digitou.\n"
              "• Várias pessoas podem transmitir na mesma sala; a página mostra as telas lado a lado.",
        inline=False,
    )
    guia.set_footer(text="Este canal é atualizado automaticamente pelo bot.")
    return guia


def _conteudo(embed: discord.Embed) -> tuple:
    """Só o que aparece na tela: o embed devolvido pelo Discord traz campos extras ("type", ...)."""
    return (embed.title, embed.description, embed.footer.text,
            tuple((f.name, f.value) for f in embed.fields))


async def garantir_canal(servidor: discord.Guild) -> str:
    """Cria o canal do guia (ou atualiza a mensagem dele). Devolve o que foi feito, para o log."""
    eu = servidor.me
    permissoes = eu.guild_permissions
    canal = discord.utils.get(servidor.text_channels, name=NOME_CANAL)
    guia = montar_guia()

    if canal is None:
        if not permissoes.manage_channels:
            return "sem permissão 'Gerenciar canais' para criar o canal do guia"
        canal = await servidor.create_text_channel(
            NOME_CANAL,
            topic=TOPICO,
            overwrites={
                # Somente leitura para os membros: o canal é só o guia.
                servidor.default_role: discord.PermissionOverwrite(
                    view_channel=True, send_messages=False, add_reactions=False,
                    create_public_threads=False, create_private_threads=False),
                eu: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, embed_links=True, read_message_history=True),
            },
            reason="Guia do bot Live dos Dude",
        )
        await canal.send(embed=guia)
        return f"canal #{NOME_CANAL} criado"

    if not canal.permissions_for(eu).read_message_history:
        return f"#{NOME_CANAL} existe, mas falta 'Ler histórico de mensagens' para atualizar o guia"
    async for mensagem in canal.history(limit=20):
        if mensagem.author.id == eu.id and mensagem.embeds:
            if _conteudo(mensagem.embeds[0]) == _conteudo(guia):
                return "guia já atualizado"
            await mensagem.edit(embed=guia)
            return "guia atualizado"
    await canal.send(embed=guia)
    return "guia publicado no canal existente"
