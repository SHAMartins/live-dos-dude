"""Argumentos de conexão e geração de tokens compartilhados pelas ferramentas."""

from __future__ import annotations

import argparse
import os
from datetime import timedelta

from livekit import api

# Valores do LiveKit local (infra_sfu/dev). Em produção, passe por flag ou variável de ambiente.
DEV_URL = "ws://localhost:7880"
DEV_API_KEY = "devkey"
DEV_API_SECRET = "devsecret_local_apenas_para_testes_0123456789"


def adicionar_args_conexao(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=os.getenv("LIVEKIT_URL", DEV_URL),
                        help="ws://localhost:7880 ou wss://live.seudominio.com (env LIVEKIT_URL)")
    parser.add_argument("--api-key", default=os.getenv("LIVEKIT_API_KEY", DEV_API_KEY),
                        help="env LIVEKIT_API_KEY")
    parser.add_argument("--api-secret", default=os.getenv("LIVEKIT_API_SECRET", DEV_API_SECRET),
                        help="env LIVEKIT_API_SECRET")
    parser.add_argument("--sala", default="smoke-test", help="nome da sala (padrão: smoke-test)")


def criar_token(args: argparse.Namespace, identidade: str, *, publicar: bool,
                validade: timedelta = timedelta(hours=1)) -> str:
    grants = api.VideoGrants(
        room_join=True,
        room=args.sala,
        can_publish=publicar,
        can_subscribe=not publicar,
        can_publish_data=False,
    )
    return (
        api.AccessToken(args.api_key, args.api_secret)
        .with_identity(identidade)
        .with_grants(grants)
        .with_ttl(validade)
        .to_jwt()
    )
