"""Interface (janela customtkinter + bandeja pystray)."""

import os
import sys
from pathlib import Path


def _corrigir_tcl_em_venv() -> None:
    """Python 3.13.0 no Windows: dentro de uma venv o Tk não acha o init.tcl (corrigido em
    versões seguintes do 3.13). Aponta para o Tcl da instalação base, se for o caso."""
    if sys.platform != "win32" or "TCL_LIBRARY" in os.environ or sys.prefix == sys.base_prefix:
        return
    tcl = Path(sys.base_prefix) / "tcl"
    if (tcl / "tcl8.6" / "init.tcl").exists():
        os.environ["TCL_LIBRARY"] = str(tcl / "tcl8.6")
        os.environ.setdefault("TK_LIBRARY", str(tcl / "tk8.6"))


_corrigir_tcl_em_venv()  # antes de qualquer import do tkinter/customtkinter
