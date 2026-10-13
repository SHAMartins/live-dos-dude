from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

PASTA_DADOS = Path(os.environ.get("APPDATA", Path.home())) / "LiveDosDude"
# O backend de produção: quem baixa o executável só precisa do código do /parear.
BACKEND_PADRAO = "wss://livedosdude-api.duckdns.org/ws/cliente"

# Caixa de cada opção de resolução; "nativa" não reduz (é a mais barata para a CPU).
CAIXAS = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080), "nativa": (100_000, 100_000)}
OPCOES_FPS = (15, 30, 60)
BITRATE_MIN, BITRATE_MAX = 500, 8000


def bitrate_sugerido(resolucao: str, fps: int) -> int:
    """Ponto de partida razoável para conteúdo de tela em VP8 (kbps)."""
    base = {"480p": 1200, "720p": 2500, "1080p": 4500, "nativa": 4500}[resolucao]
    return int(min(BITRATE_MAX, base * {15: 0.7, 30: 1.0, 60: 1.6}.get(fps, 1.0)) // 100 * 100)


@dataclass
class ConfigCliente:
    backend_url: str = BACKEND_PADRAO
    nome_dispositivo: str = field(default_factory=socket.gethostname)
    # O que transmitir (escolhido no seletor da janela). None = tela principal.
    # Formatos em cliente/media/fontes.py.
    fonte: dict | None = None
    # Padrões de qualidade. O que vier digitado no /transmitir vale só naquela vez.
    resolucao: Literal["480p", "720p", "1080p", "nativa"] = "720p"
    fps: int = 30
    bitrate_kbps: int = 2500
    # Som do sistema junto com o vídeo (todo o som do PC, menos o Discord).
    audio: bool = True
    # O que sacrificar quando falta banda ou CPU: "resolucao" mantém o fps (jogos),
    # "fps" mantém a nitidez (texto, código).
    sacrificar: Literal["resolucao", "fps"] = "fps"
    # O que o ✕ da janela faz: "perguntar", "bandeja" (continua rodando, para receber o /transmitir) ou "sair".
    ao_fechar: Literal["perguntar", "bandeja", "sair"] = "perguntar"

    @classmethod
    def carregar(cls, caminho: Path = PASTA_DADOS / "config.json") -> ConfigCliente:
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8-sig"))  # utf-8-sig: aceita BOM (Bloco de Notas, PowerShell)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as erro:
            log.warning("config.json ilegível (%s); usando os padrões", erro)
            return cls()
        conhecidos = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dados.items() if k in conhecidos})

    def salvar(self, caminho: Path = PASTA_DADOS / "config.json") -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
