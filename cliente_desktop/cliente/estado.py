"""Estado do cliente, compartilhado entre a thread asyncio (que escreve) e a UI (que lê)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from enum import StrEnum


class Estado(StrEnum):
    INICIANDO = "iniciando"
    CONECTANDO = "conectando"          # tentando conectar ou esperando para tentar de novo
    PRECISA_PAREAR = "precisa_parear"  # sem token válido: precisa do código do /parear
    ONLINE = "online"
    TRANSMITINDO = "transmitindo"
    BLOQUEADO = "bloqueado"            # 4002/4003: não reconecta sozinho


@dataclass(frozen=True)
class Snapshot:
    estado: Estado = Estado.INICIANDO
    mensagem: str = ""
    usuario: str | None = None
    dispositivo: str | None = None
    sala: str | None = None
    resolucao: tuple[int, int] | None = None
    fps_alvo: int | None = None
    bitrate_kbps: int | None = None
    codec: str | None = None
    captura: str | None = None  # "dxgi", "gdi" ou "janela"
    fonte: str | None = None    # descrição do que está sendo transmitido
    audio: bool | None = None   # o som do PC está indo junto?

    def com(self, **mudancas) -> Snapshot:
        return replace(self, **mudancas)


class PainelEstado:
    """Último snapshot publicado. A UI consulta pela versão para só redesenhar quando muda."""

    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._snapshot = Snapshot()
        self._versao = 0

    def publicar(self, snapshot: Snapshot) -> None:
        with self._trava:
            self._snapshot = snapshot
            self._versao += 1

    def ler(self) -> tuple[int, Snapshot]:
        with self._trava:
            return self._versao, self._snapshot
