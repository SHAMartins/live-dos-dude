"""Onde o device_token fica guardado. Um token por URL de backend."""

from __future__ import annotations

from typing import Protocol

SERVICO = "live-dos-dude"


class Credenciais(Protocol):
    def ler(self, backend_url: str) -> str | None: ...
    def salvar(self, backend_url: str, token: str) -> None: ...
    def apagar(self, backend_url: str) -> None: ...


class CredenciaisWindows:
    """Gerenciador de Credenciais do Windows (via keyring): o token não fica em arquivo."""

    def __init__(self) -> None:
        import keyring  # import tardio: os testes usam CredenciaisMemoria

        self._keyring = keyring

    def ler(self, backend_url: str) -> str | None:
        return self._keyring.get_password(SERVICO, backend_url)

    def salvar(self, backend_url: str, token: str) -> None:
        self._keyring.set_password(SERVICO, backend_url, token)

    def apagar(self, backend_url: str) -> None:
        try:
            self._keyring.delete_password(SERVICO, backend_url)
        except self._keyring.errors.PasswordDeleteError:
            pass


class CredenciaisMemoria:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    def ler(self, backend_url: str) -> str | None:
        return self.tokens.get(backend_url)

    def salvar(self, backend_url: str, token: str) -> None:
        self.tokens[backend_url] = token

    def apagar(self, backend_url: str) -> None:
        self.tokens.pop(backend_url, None)
