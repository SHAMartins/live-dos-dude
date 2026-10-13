"""Definição dos slash commands (sem conectar no Discord)."""

from __future__ import annotations

from types import SimpleNamespace

from app.bot.cliente import BotDiscord
from app.bot.comandos import tem_permissao
from app.config import Config


def _opcoes(comando: dict) -> dict[str, dict]:
    return {o["name"]: o for o in comando.get("options", [])}


async def test_comandos_registrados_com_opcoes_e_escolhas(ambiente):
    bot = BotDiscord(ambiente.servico, Config(_env_file=None))
    try:
        comandos = {c.name: c.to_dict(bot.tree) for c in bot.tree.get_commands()}
        assert set(comandos) == {"parear", "transmitir", "parar", "status", "assistir"}

        opcoes = _opcoes(comandos["transmitir"])
        assert opcoes["sala"]["required"] and opcoes["sala"]["autocomplete"]
        assert [c["value"] for c in opcoes["resolucao"]["choices"]] == ["480p", "720p", "1080p"]
        assert [c["value"] for c in opcoes["fps"]["choices"]] == [15, 30, 60]
        assert (opcoes["bitrate"]["min_value"], opcoes["bitrate"]["max_value"]) == (300, 8000)
        assert not any(opcoes[n].get("required") for n in ("resolucao", "fps", "bitrate"))
        assert "codec" not in opcoes  # H.264 por software desaba com imagem variada: não é mais uma opção
        assert comandos["transmitir"]["contexts"] == [0]  # guild_only
    finally:
        await bot.close()


def test_permissao_por_cargo():
    membro = SimpleNamespace(roles=[SimpleNamespace(id=42, name="Streamer")])
    assert tem_permissao(membro, None)
    assert tem_permissao(membro, "Streamer")
    assert tem_permissao(membro, "42")
    assert not tem_permissao(membro, "Admin")
    assert not tem_permissao(SimpleNamespace(), "Streamer")  # usuário fora de servidor não tem cargos


def test_guia_cita_todos_os_comandos_e_cabe_nos_limites_do_discord():
    from app.bot.guia import COMANDOS, montar_guia

    guia = montar_guia()
    texto = " ".join(f"{f.name} {f.value}" for f in guia.fields)
    for nome in ("parear", "transmitir", "parar", "status", "assistir"):
        assert f"/{nome}" in texto, nome
    assert len(COMANDOS) == 5
    assert len(guia) <= 6000 and len(guia.fields) <= 25
    assert all(len(f.value) <= 1024 and len(f.name) <= 256 for f in guia.fields)


class _Historico:
    def __init__(self, mensagens):
        self._mensagens = mensagens

    def __call__(self, limit):
        async def gerar():
            for m in self._mensagens:
                yield m
        return gerar()


def _servidor(canais, pode_gerenciar=True, pode_ler=True):
    from unittest.mock import AsyncMock, MagicMock

    eu = MagicMock(id=999)
    eu.guild_permissions.manage_channels = pode_gerenciar
    servidor = MagicMock(me=eu, text_channels=canais)
    criado = MagicMock()
    criado.name, criado.send = "live-dos-dude", AsyncMock()
    servidor.create_text_channel = AsyncMock(return_value=criado)
    for canal in canais:
        canal.permissions_for.return_value.read_message_history = pode_ler
    return servidor, criado


async def test_cria_o_canal_somente_leitura_com_o_guia():
    from app.bot.guia import NOME_CANAL, garantir_canal

    servidor, criado = _servidor([])
    assert await garantir_canal(servidor) == f"canal #{NOME_CANAL} criado"
    args, kwargs = servidor.create_text_channel.call_args
    assert args[0] == NOME_CANAL
    regras_de_todos = kwargs["overwrites"][servidor.default_role]
    assert regras_de_todos.send_messages is False and regras_de_todos.view_channel is True
    assert criado.send.await_args.kwargs["embed"].title == "📺 Live dos Dude"


async def test_canal_existente_atualiza_a_mensagem_em_vez_de_duplicar():
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from app.bot.guia import garantir_canal, montar_guia

    antigo = discord.Embed(title="📺 Live dos Dude", description="versão velha")
    mensagem = MagicMock(embeds=[antigo], edit=AsyncMock())
    mensagem.author.id = 999
    canal = MagicMock(send=AsyncMock())
    canal.name = "live-dos-dude"
    canal.history = _Historico([mensagem])
    servidor, _ = _servidor([canal])

    assert await garantir_canal(servidor) == "guia atualizado"
    mensagem.edit.assert_awaited_once()
    canal.send.assert_not_awaited()

    mensagem.embeds = [montar_guia()]
    assert await garantir_canal(servidor) == "guia já atualizado"


async def test_sem_permissao_nao_quebra():
    from app.bot.guia import garantir_canal

    servidor, _ = _servidor([], pode_gerenciar=False)
    assert "sem permissão" in await garantir_canal(servidor)
    servidor.create_text_channel.assert_not_awaited()


def test_varios_servidores_separados_por_virgula():
    assert Config(_env_file=None, discord_guild_id="111, 222").servidores_discord == [111, 222]
    assert Config(_env_file=None, discord_guild_id="333").servidores_discord == [333]
    assert Config(_env_file=None).servidores_discord == []
