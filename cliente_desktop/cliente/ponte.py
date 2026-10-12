"""Thread do event loop asyncio (WebSocket + LiveKit), separada da UI que roda na thread principal."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable, Coroutine
from typing import Any


class ThreadAsyncio:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._rodar, name="asyncio", daemon=True)

    def iniciar(self) -> None:
        self._thread.start()

    def submeter(self, corrotina: Coroutine[Any, Any, Any]) -> concurrent.futures.Future:
        """Agenda uma corrotina a partir de outra thread (UI, bandeja)."""
        return asyncio.run_coroutine_threadsafe(corrotina, self.loop)

    def chamar(self, funcao: Callable[..., Any], *args: Any) -> None:
        """Executa uma função síncrona dentro da thread do loop."""
        self.loop.call_soon_threadsafe(funcao, *args)

    def parar(self, timeout: float = 5.0) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout)

    def _rodar(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
