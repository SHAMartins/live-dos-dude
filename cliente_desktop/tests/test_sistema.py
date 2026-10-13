"""Integração com o Windows, sem tocar no registro de verdade."""

from __future__ import annotations

import json
import sys
import types

import pytest

from cliente import sistema
from cliente.config import BACKEND_PADRAO, ConfigCliente


def test_iniciar_com_windows_so_no_executavel(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    with pytest.raises(RuntimeError, match="executável"):
        sistema.definir_iniciar_com_windows(True)


@pytest.mark.skipif(sys.platform != "win32", reason="registro do Windows")
def test_iniciar_com_windows_grava_e_apaga_o_valor(monkeypatch):
    gravado: dict[str, str] = {}

    class ChaveFalsa:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    falso = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), KEY_SET_VALUE=2, REG_SZ=1,
        OpenKey=lambda *a, **k: ChaveFalsa(),
        SetValueEx=lambda chave, nome, _r, _t, valor: gravado.__setitem__(nome, valor),
        DeleteValue=lambda chave, nome: gravado.pop(nome),
        QueryValueEx=lambda chave, nome: (gravado[nome], 1) if nome in gravado else (_ for _ in ()).throw(OSError()),
    )
    monkeypatch.setitem(sys.modules, "winreg", falso)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\LiveDosDude\LiveDosDude.exe")

    sistema.definir_iniciar_com_windows(True)
    assert gravado == {"LiveDosDude": r'"C:\Apps\LiveDosDude\LiveDosDude.exe"'}
    assert sistema.iniciar_com_windows_ativo()
    sistema.definir_iniciar_com_windows(False)
    assert gravado == {} and not sistema.iniciar_com_windows_ativo()


def test_config_padrao_aponta_para_producao_e_aceita_bom(tmp_path):
    assert ConfigCliente().backend_url == BACKEND_PADRAO
    caminho = tmp_path / "config.json"
    caminho.write_bytes(b"\xef\xbb\xbf" + json.dumps({"fps": 60}).encode())  # como o Bloco de Notas salva
    assert ConfigCliente.carregar(caminho).fps == 60


def test_ao_fechar_padrao_pergunta_e_persiste(tmp_path):
    from cliente.config import ConfigCliente
    from cliente.ui.fechamento import escolha_valida

    caminho = tmp_path / "config.json"
    config = ConfigCliente()
    assert config.ao_fechar == "perguntar"
    config.ao_fechar = "sair"
    config.salvar(caminho)
    assert ConfigCliente.carregar(caminho).ao_fechar == "sair"
    assert escolha_valida("bandeja") == "bandeja"
    assert escolha_valida("qualquer coisa") == "perguntar"  # config.json editado à mão
