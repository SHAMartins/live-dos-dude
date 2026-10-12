"""Mensagens do WebSocket backend <-> cliente desktop. Contrato: docs/protocolo.md."""

from __future__ import annotations

from enum import IntEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

VERSAO = 1


class _Mensagem(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class ParametrosVideo(_Mensagem):
    largura: int
    altura: int
    fps: int
    bitrate_kbps: int
    codec: Literal["vp8", "h264"]


CampoVideo = Literal["resolucao", "fps", "bitrate_kbps", "codec"]


# --- cliente -> backend: handshake -------------------------------------------

class Hello(_Mensagem):
    tipo: Literal["hello"] = "hello"
    v: int
    device_token: str = Field(min_length=16, max_length=128)
    versao_cliente: str = Field(default="?", max_length=32)
    sessao_ativa: str | None = Field(default=None, max_length=64)


class Parear(_Mensagem):
    tipo: Literal["parear"] = "parear"
    v: int
    codigo: str = Field(min_length=4, max_length=16)
    nome_dispositivo: str = Field(min_length=1, max_length=64)


# --- cliente -> backend: depois do handshake ---------------------------------

class Ack(_Mensagem):
    tipo: Literal["ack"] = "ack"
    id: str


class Resultado(_Mensagem):
    tipo: Literal["resultado"] = "resultado"
    id: str
    ok: bool
    detalhe: str = Field(default="", max_length=500)
    # No 'iniciar': os parâmetros realmente aplicados (o PC pode ter usado os padrões dele).
    video: ParametrosVideo | None = None


class TransmissaoEncerrada(_Mensagem):
    tipo: Literal["transmissao_encerrada"] = "transmissao_encerrada"
    sessao_id: str
    motivo: str = Field(default="", max_length=200)


class TransmissaoAtualizada(_Mensagem):
    """O usuário mudou fonte/qualidade no PC com a transmissão no ar (mesma sessão, mesmo link)."""

    tipo: Literal["transmissao_atualizada"] = "transmissao_atualizada"
    sessao_id: str
    video: ParametrosVideo


HANDSHAKE = TypeAdapter(Annotated[Hello | Parear, Field(discriminator="tipo")])
DO_CLIENTE = TypeAdapter(
    Annotated[Ack | Resultado | TransmissaoEncerrada | TransmissaoAtualizada, Field(discriminator="tipo")]
)


# --- backend -> cliente -------------------------------------------------------

class BemVindo(_Mensagem):
    tipo: Literal["bem_vindo"] = "bem_vindo"
    usuario: str
    dispositivo: str


class Pareado(_Mensagem):
    tipo: Literal["pareado"] = "pareado"
    device_token: str
    usuario: str
    dispositivo: str


class Iniciar(_Mensagem):
    tipo: Literal["iniciar"] = "iniciar"
    id: str  # também é o id da sessão
    sala: str
    livekit_url: str
    token: str
    video: ParametrosVideo
    # Quais parâmetros vieram do /transmitir; os outros o PC pode trocar pelos padrões dele.
    # None (backend antigo) = todos vieram do comando.
    definidos: list[CampoVideo] | None = None


class Parar(_Mensagem):
    tipo: Literal["parar"] = "parar"
    id: str
    sessao_id: str


class Erro(_Mensagem):
    tipo: Literal["erro"] = "erro"
    codigo: str
    mensagem: str


Comando = Iniciar | Parar


class Fechamento(IntEnum):
    REINICIANDO = 1012  # padrão do WebSocket ("service restart"): o cliente reconecta com backoff
    PROTOCOLO_INVALIDO = 4000
    NAO_AUTENTICADO = 4001
    SUBSTITUIDO = 4002
    VERSAO_INCOMPATIVEL = 4003
