"""Cópia consistente do SQLite (funciona com o backend rodando, inclusive em modo WAL).

    python -m app.backup                      # dados/backups/, mantém os 14 mais recentes
    python -m app.backup --pasta /x --manter 30

Na VM: docker compose exec -T backend python -m app.backup (veja backend_bot/README.md).
"""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.config import Config


def fazer_backup(banco: Path, pasta: Path, manter: int) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"backend-{datetime.now(UTC):%Y%m%d-%H%M%S-%f}.sqlite3"  # %f: dois backups no mesmo segundo não se sobrescrevem
    # A API de backup do SQLite copia página a página com o banco em uso, sem pegar
    # um arquivo pela metade (copiar o .sqlite3 com cp poderia).
    # closing(): o "with" do sqlite3 só faz commit, não fecha a conexão.
    with closing(sqlite3.connect(banco)) as origem, closing(sqlite3.connect(destino)) as copia:
        origem.backup(copia)
    for antigo in sorted(pasta.glob("backend-*.sqlite3"), reverse=True)[manter:]:
        antigo.unlink()
    return destino


def main() -> None:
    config = Config()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pasta", type=Path, default=config.banco.parent / "backups")
    parser.add_argument("--manter", type=int, default=14)
    args = parser.parse_args()
    print(fazer_backup(config.banco, args.pasta, args.manter))


if __name__ == "__main__":
    main()
