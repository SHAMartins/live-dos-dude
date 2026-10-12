"""Integração com o Windows: executável empacotado, "iniciar com o Windows" e ícone."""

from __future__ import annotations

import sys
from pathlib import Path

NOME_APP = "LiveDosDude"
CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def empacotado() -> bool:
    """True quando roda como LiveDosDude.exe (PyInstaller), False rodando com python -m cliente."""
    return bool(getattr(sys, "frozen", False))


def recurso(nome: str) -> Path:
    """Arquivo que vai junto do app (ícone etc.), tanto empacotado quanto em desenvolvimento."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent / "empacotamento"))
    return base / nome


def comando_de_inicio() -> str:
    return f'"{sys.executable}"'


def iniciar_com_windows_ativo() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as chave:
            valor, _ = winreg.QueryValueEx(chave, NOME_APP)
        return valor == comando_de_inicio()
    except OSError:
        return False


def definir_iniciar_com_windows(ativo: bool) -> None:
    """Só para o usuário atual (HKCU), sem precisar de administrador. Só faz sentido no .exe."""
    if sys.platform != "win32" or not empacotado():
        raise RuntimeError("disponível só no executável instalado")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as chave:
        if ativo:
            winreg.SetValueEx(chave, NOME_APP, 0, winreg.REG_SZ, comando_de_inicio())
        else:
            try:
                winreg.DeleteValue(chave, NOME_APP)
            except FileNotFoundError:
                pass


def identificar_na_barra_de_tarefas() -> None:
    """Agrupa as janelas do app sob o ícone dele na barra de tarefas (e não sob o do Python)."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LiveDosDude.Cliente")
