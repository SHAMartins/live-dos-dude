"""Backup do SQLite com o backend rodando."""

from __future__ import annotations

import sqlite3

from app.backup import fazer_backup
from app.services.transmissao import PedidoTransmissao
from auxiliares import FULANO


async def test_backup_com_o_banco_em_uso_e_rotacao(ambiente, cliente_pareado, tmp_path):
    await cliente_pareado()
    sessao = await ambiente.servico.transmitir(FULANO, PedidoTransmissao("dev"))
    pasta = tmp_path / "backups"

    copia = fazer_backup(ambiente.config.banco, pasta, manter=2)
    with sqlite3.connect(copia) as banco:
        assert banco.execute("SELECT sala FROM sessoes WHERE id = ?", (sessao.id,)).fetchone() == ("dev",)

    for i in range(3):  # backups "antigos", que devem sair primeiro na rotação
        (pasta / f"backend-2000010{i}-000000.sqlite3").write_bytes(b"")
    fazer_backup(ambiente.config.banco, pasta, manter=2)
    assert len(list(pasta.glob("backend-*.sqlite3"))) == 2
    assert not list(pasta.glob("backend-2000*.sqlite3"))  # os mais antigos saem primeiro
