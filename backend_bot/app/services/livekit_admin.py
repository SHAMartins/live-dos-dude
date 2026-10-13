"""Chamadas administrativas ao LiveKit (API HTTP do servidor) e validação dos webhooks."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit

from livekit import api

from app.config import Config

log = logging.getLogger(__name__)

# Vida da sala vazia. O LiveKit padrão espera ~5 min com a sala sem ninguém; aqui ela some logo.
EMPTY_TIMEOUT_S = 120      # ninguém entrou depois de criada
DEPARTURE_TIMEOUT_S = 15   # o último participante saiu
PRAZO_ADMIN_S = 4          # a API administrativa não pode travar o /transmitir nem o /parar


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

    async def criar_sala(self, sala: str) -> bool:
        """Cria a sala já com tempos curtos de vida vazia. Idempotente; falhar não impede a transmissão
        (o LiveKit cria a sala sozinho quando o PC entra, só que com os tempos padrão)."""
        try:
            async with api.LiveKitAPI(self._url, self._key, self._secret) as lk:
                await asyncio.wait_for(lk.room.create_room(api.CreateRoomRequest(
                    name=sala, empty_timeout=EMPTY_TIMEOUT_S, departure_timeout=DEPARTURE_TIMEOUT_S)), PRAZO_ADMIN_S)
            return True
        except Exception as erro:  # noqa: BLE001 - LiveKit fora do ar não pode quebrar o /transmitir
            log.warning("não foi possível criar a sala %s no LiveKit: %s", sala, erro)
            return False

    async def fechar_sala(self, sala: str) -> bool:
        """Fecha a sala no servidor (quem ainda estiver nela é desconectado). False se já não existia."""
        try:
            async with api.LiveKitAPI(self._url, self._key, self._secret) as lk:
                await asyncio.wait_for(lk.room.delete_room(api.DeleteRoomRequest(room=sala)), PRAZO_ADMIN_S)
            return True
        except api.TwirpError as erro:
            if erro.code != api.TwirpErrorCode.NOT_FOUND:
                log.warning("LiveKit recusou fechar a sala %s: %s", sala, erro)
            return False
        except Exception as erro:  # noqa: BLE001
            log.warning("não foi possível fechar a sala %s no LiveKit: %s", sala, erro)
            return False

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
