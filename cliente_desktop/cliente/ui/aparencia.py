"""Cores, textos e o ícone de cada estado: compartilhados pela janela e pela bandeja."""

from __future__ import annotations

from functools import cache

from PIL import Image, ImageDraw

from cliente.estado import Estado

COR = {
    Estado.INICIANDO: "#d4a017",
    Estado.CONECTANDO: "#d4a017",
    Estado.PRECISA_PAREAR: "#3b82f6",
    Estado.ONLINE: "#22a559",
    Estado.TRANSMITINDO: "#e5484d",
    Estado.BLOQUEADO: "#8b8d98",
}

TITULO = {
    Estado.INICIANDO: "Iniciando",
    Estado.CONECTANDO: "Conectando",
    Estado.PRECISA_PAREAR: "Precisa parear",
    Estado.ONLINE: "Online, aguardando /transmitir",
    Estado.TRANSMITINDO: "Ao vivo",
    Estado.BLOQUEADO: "Desconectado",
}


@cache
def icone(estado: Estado, tamanho: int = 64) -> Image.Image:
    """Círculo na cor do estado com um monitor branco dentro."""
    imagem = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(imagem)
    desenho.ellipse((0, 0, tamanho - 1, tamanho - 1), fill=COR[estado])
    m = tamanho / 64
    desenho.rounded_rectangle((15 * m, 18 * m, 49 * m, 40 * m), radius=3 * m, outline="white", width=round(4 * m))
    desenho.rectangle((29 * m, 40 * m, 35 * m, 45 * m), fill="white")
    desenho.rectangle((22 * m, 45 * m, 42 * m, 48 * m), fill="white")
    return imagem
