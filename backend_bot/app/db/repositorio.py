from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aiosqlite

SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
ESTADOS_EM_ANDAMENTO = ("solicitada", "ativa")
_COLUNAS_SESSAO = "id, discord_id, sala, resolucao, fps, bitrate_kbps, codec, estado, detalhe, iniciada_em, chave_espectador"

# Versão -> comandos. Bancos antigos sobem de versão ao abrir; nunca edite uma migração já publicada.
MIGRACOES: dict[int, tuple[str, ...]] = {
    2: (
        "ALTER TABLE sessoes ADD COLUMN chave_espectador TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS sessoes_por_chave ON sessoes (chave_espectador)",
    ),
}


@dataclass(frozen=True)
class Dispositivo:
    id: int
    discord_id: int
    nome: str
    usuario_nome: str


@dataclass(frozen=True)
class Sala:
    nome: str
    resolucao: str
    fps: int
    bitrate_kbps: int
    codec: str


@dataclass(frozen=True)
class Sessao:
    id: str
    discord_id: int
    sala: str
    resolucao: str
    fps: int
    bitrate_kbps: int
    codec: str
    estado: str
    detalhe: str
    iniciada_em: str  # UTC, 'AAAA-MM-DD HH:MM:SS'
    chave_espectador: str | None


class Repositorio:
    """Acesso ao SQLite sem bloquear o event loop (aiosqlite roda as queries numa thread)."""

    def __init__(self, conexao: aiosqlite.Connection) -> None:
        self._db = conexao

    @classmethod
    async def abrir(cls, caminho: Path | str) -> Repositorio:
        if str(caminho) != ":memory:":
            Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        conexao = await aiosqlite.connect(caminho)
        conexao.row_factory = aiosqlite.Row
        await conexao.execute("PRAGMA journal_mode = WAL")
        await conexao.execute("PRAGMA foreign_keys = ON")
        await conexao.executescript(SCHEMA)
        await _migrar(conexao)
        await conexao.commit()
        return cls(conexao)

    async def fechar(self) -> None:
        await self._db.close()

    # --- usuários -------------------------------------------------------------

    async def salvar_usuario(self, discord_id: int, nome: str) -> None:
        await self._db.execute(
            "INSERT INTO usuarios (discord_id, nome) VALUES (?, ?) "
            "ON CONFLICT (discord_id) DO UPDATE SET nome = excluded.nome",
            (discord_id, nome),
        )
        await self._db.commit()

    # --- pareamento -----------------------------------------------------------

    async def criar_codigo(self, discord_id: int, codigo: str, expira_em: float) -> None:
        """Um código ativo por usuário: gerar outro invalida o anterior."""
        await self._db.execute("DELETE FROM codigos_pareamento WHERE discord_id = ?", (discord_id,))
        await self._db.execute(
            "INSERT INTO codigos_pareamento (codigo, discord_id, expira_em) VALUES (?, ?, ?)",
            (codigo, discord_id, expira_em),
        )
        await self._db.commit()

    async def consumir_codigo(self, codigo: str, agora: float) -> int | None:
        """Apaga o código (uso único) e devolve o discord_id dono, se ainda válido."""
        await self._db.execute("DELETE FROM codigos_pareamento WHERE expira_em < ?", (agora,))
        cursor = await self._db.execute(
            "DELETE FROM codigos_pareamento WHERE codigo = ? RETURNING discord_id", (codigo,)
        )
        linha = await cursor.fetchone()
        await cursor.close()
        await self._db.commit()
        return linha["discord_id"] if linha else None

    async def salvar_dispositivo(self, discord_id: int, nome: str, token_hash: str) -> None:
        await self._db.execute(
            "INSERT INTO dispositivos (discord_id, nome, token_hash) VALUES (?, ?, ?) "
            "ON CONFLICT (discord_id) DO UPDATE SET nome = excluded.nome, "
            "token_hash = excluded.token_hash, pareado_em = CURRENT_TIMESTAMP, visto_em = NULL",
            (discord_id, nome, token_hash),
        )
        await self._db.commit()

    async def _dispositivo(self, where: str, valor: object) -> Dispositivo | None:
        cursor = await self._db.execute(
            "SELECT d.id, d.discord_id, d.nome, u.nome AS usuario_nome "
            f"FROM dispositivos d JOIN usuarios u USING (discord_id) WHERE {where} = ?",
            (valor,),
        )
        linha = await cursor.fetchone()
        await cursor.close()
        return Dispositivo(**dict(linha)) if linha else None

    async def dispositivo_por_token(self, token_hash: str) -> Dispositivo | None:
        return await self._dispositivo("d.token_hash", token_hash)

    async def dispositivo_do_usuario(self, discord_id: int) -> Dispositivo | None:
        return await self._dispositivo("d.discord_id", discord_id)

    async def marcar_visto(self, dispositivo_id: int) -> None:
        await self._db.execute(
            "UPDATE dispositivos SET visto_em = CURRENT_TIMESTAMP WHERE id = ?", (dispositivo_id,)
        )
        await self._db.commit()

    # --- salas ----------------------------------------------------------------

    async def obter_ou_criar_sala(self, nome: str, criada_por: int) -> Sala:
        await self._db.execute(
            "INSERT INTO salas (nome, criada_por) VALUES (?, ?) ON CONFLICT (nome) DO NOTHING",
            (nome, criada_por),
        )
        await self._db.commit()
        cursor = await self._db.execute(
            "SELECT nome, resolucao, fps, bitrate_kbps, codec FROM salas WHERE nome = ?", (nome,)
        )
        linha = await cursor.fetchone()
        await cursor.close()
        return Sala(**dict(linha))

    async def listar_salas(self, prefixo: str, limite: int = 25) -> list[str]:
        cursor = await self._db.execute(
            "SELECT nome FROM salas WHERE nome LIKE ? ESCAPE '\\' ORDER BY nome LIMIT ?",
            (_escapar_like(prefixo) + "%", limite),
        )
        linhas = await cursor.fetchall()
        await cursor.close()
        return [linha["nome"] for linha in linhas]

    # --- sessões --------------------------------------------------------------

    async def criar_sessao(self, sessao_id: str, discord_id: int, sala: str, resolucao: str,
                           fps: int, bitrate_kbps: int, codec: str, chave_espectador: str) -> None:
        await self._db.execute(
            "INSERT INTO sessoes (id, discord_id, sala, resolucao, fps, bitrate_kbps, codec, estado, chave_espectador) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'solicitada', ?)",
            (sessao_id, discord_id, sala, resolucao, fps, bitrate_kbps, codec, chave_espectador),
        )
        await self._db.commit()

    async def atualizar_sessao(self, sessao_id: str, estado: str, detalhe: str = "",
                               *, discord_id: int | None = None) -> bool:
        """Muda o estado. Com discord_id, só altera se a sessão for desse usuário."""
        encerrou = estado not in ESTADOS_EM_ANDAMENTO
        cursor = await self._db.execute(
            "UPDATE sessoes SET estado = ?, detalhe = ?, "
            "encerrada_em = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END "
            "WHERE id = ? AND (? IS NULL OR discord_id = ?)",
            (estado, detalhe, encerrou, sessao_id, discord_id, discord_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def atualizar_parametros_sessao(self, sessao_id: str, resolucao: str, fps: int,
                                          bitrate_kbps: int, codec: str) -> None:
        await self._db.execute(
            "UPDATE sessoes SET resolucao = ?, fps = ?, bitrate_kbps = ?, codec = ? WHERE id = ?",
            (resolucao, fps, bitrate_kbps, codec, sessao_id),
        )
        await self._db.commit()

    async def encerrar_sessoes_do_usuario(self, discord_id: int, detalhe: str,
                                          *, exceto: str | None = None) -> int:
        cursor = await self._db.execute(
            "UPDATE sessoes SET estado = 'encerrada', detalhe = ?, encerrada_em = CURRENT_TIMESTAMP "
            "WHERE discord_id = ? AND estado IN ('solicitada', 'ativa') AND id IS NOT ?",
            (detalhe, discord_id, exceto),
        )
        await self._db.commit()
        return cursor.rowcount

    async def sessao_em_andamento(self, discord_id: int) -> Sessao | None:
        cursor = await self._db.execute(
            f"SELECT {_COLUNAS_SESSAO} FROM sessoes WHERE discord_id = ? AND estado IN ('solicitada', 'ativa') "
            "ORDER BY iniciada_em DESC LIMIT 1",
            (discord_id,),
        )
        linha = await cursor.fetchone()
        await cursor.close()
        return Sessao(**dict(linha)) if linha else None

    async def sessao(self, sessao_id: str) -> Sessao | None:
        cursor = await self._db.execute(
            f"SELECT {_COLUNAS_SESSAO} FROM sessoes WHERE id = ?",
            (sessao_id,),
        )
        linha = await cursor.fetchone()
        await cursor.close()
        return Sessao(**dict(linha)) if linha else None


    async def sessao_ativa_por_chave(self, chave: str) -> tuple[Sessao, str] | None:
        """Sessão ATIVA dona da chave de espectador, com o nome de quem transmite."""
        cursor = await self._db.execute(
            f"SELECT {_prefixar(_COLUNAS_SESSAO, 's')}, u.nome AS transmissor FROM sessoes s "
            "JOIN usuarios u USING (discord_id) WHERE s.chave_espectador = ? AND s.estado = 'ativa'",
            (chave,),
        )
        linha = await cursor.fetchone()
        await cursor.close()
        if linha is None:
            return None
        dados = dict(linha)
        return Sessao(**{k: v for k, v in dados.items() if k != "transmissor"}), dados["transmissor"]

    async def ha_sessao_em_andamento_na_sala(self, sala: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM sessoes WHERE sala = ? AND estado IN ('solicitada', 'ativa') LIMIT 1", (sala,))
        linha = await cursor.fetchone()
        await cursor.close()
        return linha is not None

    async def sessoes_ativas_na_sala(self, sala: str) -> list[tuple[Sessao, str]]:
        cursor = await self._db.execute(
            f"SELECT {_prefixar(_COLUNAS_SESSAO, 's')}, u.nome AS transmissor FROM sessoes s "
            "JOIN usuarios u USING (discord_id) WHERE s.sala = ? AND s.estado = 'ativa' ORDER BY s.iniciada_em",
            (sala,),
        )
        linhas = await cursor.fetchall()
        await cursor.close()
        resultado = []
        for linha in linhas:
            dados = dict(linha)
            resultado.append((Sessao(**{k: v for k, v in dados.items() if k != "transmissor"}), dados["transmissor"]))
        return resultado


async def _migrar(conexao: aiosqlite.Connection) -> None:
    cursor = await conexao.execute("PRAGMA user_version")
    versao = (await cursor.fetchone())[0] or 1  # 0 = banco recém-criado pelo schema base (v1)
    await cursor.close()
    for alvo in sorted(MIGRACOES):
        if alvo > versao:
            for comando in MIGRACOES[alvo]:
                await conexao.execute(comando)
            await conexao.execute(f"PRAGMA user_version = {alvo}")
            versao = alvo


def _prefixar(colunas: str, tabela: str) -> str:
    return ", ".join(f"{tabela}.{c.strip()}" for c in colunas.split(","))


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
