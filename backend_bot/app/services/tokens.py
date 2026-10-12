from __future__ import annotations

import secrets
from datetime import timedelta

from livekit import api

from app.config import Config

# Só para ENTRAR na sala: depois de conectado, o próprio LiveKit renova o token.
VALIDADE_PUBLICADOR = timedelta(minutes=15)
VALIDADE_ESPECTADOR = timedelta(hours=1)


class EmissorTokens:
    """Assina JWTs do LiveKit. O API secret nunca sai do backend."""

    def __init__(self, livekit_url: str, api_key: str, api_secret: str) -> None:
        self.livekit_url = livekit_url
        self._api_key = api_key
        self._api_secret = api_secret

    @classmethod
    def de_config(cls, config: Config) -> EmissorTokens:
        return cls(config.livekit_url, config.livekit_api_key, config.livekit_api_secret.get_secret_value())

    def publicador(self, discord_id: int, nome: str, sala: str) -> str:
        grants = api.VideoGrants(
            room_join=True,
            room=sala,
            can_publish=True,
            can_subscribe=False,
            can_publish_data=False,
            can_publish_sources=["screen_share", "screen_share_audio"],
        )
        return self._assinar(f"pc-{discord_id}", nome, grants, VALIDADE_PUBLICADOR)

    def espectador(self, sala: str) -> str:
        """Espectador anônimo (entrou pelo link com chave): só assiste, só nesta sala."""
        grants = api.VideoGrants(room_join=True, room=sala, can_publish=False,
                                 can_subscribe=True, can_publish_data=False)
        return self._assinar(f"espectador-{secrets.token_hex(4)}", "Espectador", grants, VALIDADE_ESPECTADOR)

    def _assinar(self, identidade: str, nome: str, grants: api.VideoGrants, validade: timedelta) -> str:
        return (
            api.AccessToken(self._api_key, self._api_secret)
            .with_identity(identidade)
            .with_name(nome)
            .with_grants(grants)
            .with_ttl(validade)
            .to_jwt()
        )
