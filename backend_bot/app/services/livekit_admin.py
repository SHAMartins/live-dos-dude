"""Chamadas administrativas ao LiveKit (API HTTP do servidor) e validação dos webhooks."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

from livekit import api

from app.config import Config

log = logging.getLogger(__name__)


def url_http(livekit_url: str) -> str:
    """wss://live.x.com -> https://live.x.com (a API administrativa fala HTTP no mesmo endereço)."""
    partes = urlsplit(livekit_url)
    esquema = {"wss": "https", "ws": "http"}.get(partes.scheme, partes.scheme)
    return urlunsplit((esquema, partes.netloc, "", "", ""))


class AdminLiveKit:
    def __init__(self, livekit_url: str, api_key: str, api_secret: str) -> None:
        self._url = url_http(livekit_url)
        self._key, self._secret = api_key, api_secret
        self.receptor_webhook = api.WebhookReceiver(api.TokenVerifier(api_key, api_secret))

    @classmethod
    def de_config(cls, config: Config) -> AdminLiveKit:
        return cls(config.livekit_url, config.livekit_api_key, config.livekit_api_secret.get_secret_value())

    async def remover_participante(self, sala: str, identidade: str) -> bool:
        """Derruba alguém da sala pelo servidor. Devolve False se não estava lá ou se o LiveKit não respondeu."""
        try:
            async with api.LiveKitAPI(self._url, self._key, self._secret) as lk:
                await lk.room.remove_participant(api.RoomParticipantIdentity(room=sala, identity=identidade))
            return True
        except api.TwirpError as erro:
            if erro.code != api.TwirpErrorCode.NOT_FOUND:
                log.warning("LiveKit recusou remover %s de %s: %s", identidade, sala, erro)
            return False
        except Exception as erro:  # noqa: BLE001 - LiveKit fora do ar não pode quebrar o /parar
            log.warning("não foi possível falar com o LiveKit (%s): %s", self._url, erro)
            return False
