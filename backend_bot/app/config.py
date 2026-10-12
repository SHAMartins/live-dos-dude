from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Lida do ambiente e do .env. Os padrões apontam para o LiveKit local (infra_sfu/dev)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      env_ignore_empty=True, extra="ignore")

    # Discord. Sem token, sobe só a API (útil para testar com o cliente falso).
    discord_token: SecretStr | None = None
    # Servidor(es) onde os comandos são registrados na hora; vários IDs separados por vírgula.
    # Sem nenhum, o registro é global e o Discord pode levar até 1 h para mostrar os comandos.
    discord_guild_id: str | None = None
    # Nome ou ID do cargo que pode usar /transmitir. Vazio: qualquer membro.
    cargo_transmissor: str | None = None

    # LiveKit. A URL é a que o cliente desktop e os espectadores usam.
    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: SecretStr = SecretStr("devsecret_local_apenas_para_testes_0123456789")

    # Endereço público do backend, usado nos links /assistir/<chave> que o bot posta.
    url_publica: str = "http://127.0.0.1:8000"

    # API / WebSocket
    host: str = "127.0.0.1"
    porta: int = 8000
    banco: Path = Path("dados/backend.sqlite3")

    # Prazos do protocolo (docs/protocolo.md)
    timeout_ack_s: float = 5.0
    timeout_inicio_s: float = 20.0
    validade_codigo_s: int = 300

    @property
    def servidores_discord(self) -> list[int]:
        if not self.discord_guild_id:
            return []
        return [int(parte) for parte in str(self.discord_guild_id).replace(" ", "").split(",") if parte]
