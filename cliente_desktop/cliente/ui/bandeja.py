"""Ícone na bandeja do Windows (pystray), numa thread própria.

Os cliques chegam na thread da bandeja: as ações recebidas aqui só podem enfileirar
pedidos ou agendar na thread asyncio, nunca mexer no Tk diretamente.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pystray

from cliente.estado import Estado, Snapshot
from cliente.ui.aparencia import TITULO, icone


class Bandeja:
    def __init__(self, ao_abrir: Callable[[], None], ao_parar: Callable[[], None], ao_sair: Callable[[], None]) -> None:
        self._snapshot = Snapshot()
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: self._descricao(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Abrir janela", lambda: ao_abrir(), default=True),
            pystray.MenuItem("Parar transmissão", lambda: ao_parar(),
                             enabled=lambda _: self._snapshot.estado == Estado.TRANSMITINDO),
            pystray.MenuItem("Sair", lambda: ao_sair()),
        )
        self.icon = pystray.Icon("live-dos-dude", icone(Estado.INICIANDO), "Live dos Dude", menu)

    def iniciar(self) -> None:
        threading.Thread(target=self.icon.run, name="bandeja", daemon=True).start()

    def atualizar(self, snapshot: Snapshot) -> None:
        anterior, self._snapshot = self._snapshot, snapshot
        if snapshot.estado != anterior.estado:
            self.icon.icon = icone(snapshot.estado)
            self._avisar(anterior, snapshot)
        self.icon.title = f"Live dos Dude: {self._descricao()}"[:127]  # limite do Windows
        self.icon.update_menu()

    def parar(self) -> None:
        self.icon.stop()

    def _descricao(self) -> str:
        s = self._snapshot
        if s.estado == Estado.TRANSMITINDO and s.sala:
            return f"Ao vivo na sala {s.sala}"
        return TITULO[s.estado]

    def _avisar(self, anterior: Snapshot, atual: Snapshot) -> None:
        if not self.icon.HAS_NOTIFICATION:
            return
        if atual.estado == Estado.TRANSMITINDO:
            self.icon.notify(f"Transmitindo sua tela na sala {atual.sala}.", "Ao vivo")
        elif anterior.estado == Estado.TRANSMITINDO:
            self.icon.notify(atual.mensagem or "A transmissão foi encerrada.", "Transmissão encerrada")
        elif atual.estado == Estado.BLOQUEADO:
            self.icon.notify(atual.mensagem, "Live dos Dude desconectado")
