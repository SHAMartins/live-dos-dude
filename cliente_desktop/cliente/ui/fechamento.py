"""O que fazer quando a pessoa fecha a janela: manter na bandeja ou sair de vez."""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

ROTULOS = {"perguntar": "Perguntar", "bandeja": "Manter na bandeja", "sair": "Sair do app"}


def escolha_valida(valor: str) -> str:
    """Valor desconhecido no config.json (editado à mão) vira "perguntar"."""
    return valor if valor in ROTULOS else "perguntar"


class DialogoFechar(ctk.CTkToplevel):
    """Modal com a pergunta. `decidir(escolha, lembrar)` recebe "bandeja" ou "sair"; fechar o diálogo (✕)
    ou "Cancelar" não faz nada: a janela principal continua aberta."""

    def __init__(self, mestre, transmitindo: bool, decidir: Callable[[str, bool], None]) -> None:
        super().__init__(mestre)
        self.title("Fechar o Live dos Dude")
        self.resizable(False, False)
        self.transient(mestre)
        self._decidir = decidir
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="O que fazer ao fechar?", font=ctk.CTkFont(size=16, weight="bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=20, pady=(18, 6))
        texto = ("Manter na bandeja deixa o app rodando para receber o /transmitir do Discord. "
                 "Sair encerra o app de vez.")
        if transmitindo:
            texto = ("Você está transmitindo agora.\n\n"
                     "Manter na bandeja: a transmissão continua no ar.\n"
                     "Sair: a transmissão é encerrada e o app fecha.")
        ctk.CTkLabel(self, text=texto, anchor="w", justify="left", wraplength=360).grid(
            row=1, column=0, sticky="ew", padx=20, pady=(0, 10))

        self._lembrar = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self, text="Lembrar minha escolha", variable=self._lembrar).grid(
            row=2, column=0, sticky="w", padx=20, pady=(0, 12))

        botoes = ctk.CTkFrame(self, fg_color="transparent")
        botoes.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 18))
        botoes.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(botoes, text="Manter na bandeja", command=lambda: self._escolher("bandeja")).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(botoes, text="Sair do app", fg_color="#c13a3f", hover_color="#a02f33",
                      command=lambda: self._escolher("sair")).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ctk.CTkButton(botoes, text="Cancelar", fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"), command=self.destroy).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.after(120, self._focar)

    def _focar(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001 - a janela pode ter fechado nesse meio-tempo
            pass

    def _escolher(self, escolha: str) -> None:
        lembrar = bool(self._lembrar.get())
        self.destroy()
        self._decidir(escolha, lembrar)
