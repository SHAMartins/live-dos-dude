"""Gera um token de acesso ao LiveKit para testes manuais.

    python gerar_token.py --identidade fulano                  # espectador (só assiste)
    python gerar_token.py --identidade meu-pc --publicar       # transmissor
    python gerar_token.py --identidade fulano --horas 8 --url wss://live.seudominio.com ...
"""

from __future__ import annotations

import argparse
from datetime import timedelta

from comum import adicionar_args_conexao, criar_token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    adicionar_args_conexao(parser)
    parser.add_argument("--identidade", required=True, help="nome único do participante na sala")
    parser.add_argument("--publicar", action="store_true", help="permite publicar (padrão: só assistir)")
    parser.add_argument("--horas", type=float, default=1.0, help="validade do token (padrão: 1h)")
    args = parser.parse_args()

    token = criar_token(args, args.identidade, publicar=args.publicar, validade=timedelta(hours=args.horas))
    print(f"URL:   {args.url}")
    print(f"Sala:  {args.sala}")
    print(f"Token: {token}")


if __name__ == "__main__":
    main()
