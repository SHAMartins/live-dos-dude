from __future__ import annotations

import cv2
import numpy as np

# Medido (1080p -> 720p, BGRA, CPU por quadro): INTER_AREA = 20-23 ms; INTER_LINEAR = 5-9 ms (PSNR 46 dB
# contra o AREA: imperceptível). O AREA com razão não inteira é o maior gasto de CPU do cliente e, em jogo,
# disputa núcleos com o próprio jogo. Para reduções fortes (< 0,5x) o LINEAR sozinho serrilha, então
# primeiro se reduz pela metade (2:1 exato, que o LINEAR faz como média 2x2) e só depois se ajusta.
# 2 threads seguram o tempo de relógio sem roubar CPU à toa.
cv2.setNumThreads(2)


def tamanho_de_saida(largura_tela: int, altura_tela: int, largura_alvo: int, altura_alvo: int) -> tuple[int, int]:
    """Encaixa a tela na caixa pedida mantendo a proporção. Nunca aumenta; dimensões pares (exigência do I420).

    Ex.: 1920x1200 em 720p dá 1152x720, sem achatar a imagem.
    """
    fator = min(largura_alvo / largura_tela, altura_alvo / altura_tela, 1.0)
    largura = max(2, round(largura_tela * fator / 2) * 2)
    altura = max(2, round(altura_tela * fator / 2) * 2)
    return largura, altura


def _reduzir(bgra: np.ndarray, largura: int, altura: int) -> np.ndarray:
    """Redimensiona para exatamente (largura, altura), sempre devolvendo um buffer novo e gravável
    (o de entrada pode ser memória interna da captura, e o LiveKit só aceita buffers graváveis sem copiar)."""
    atual = bgra
    while atual.shape[1] >= 2 * largura and atual.shape[0] >= 2 * altura:
        atual = cv2.resize(atual, (atual.shape[1] // 2, atual.shape[0] // 2), interpolation=cv2.INTER_LINEAR)
    if atual.shape[:2] == (altura, largura):
        return atual if atual is not bgra else bgra.copy()
    return cv2.resize(atual, (largura, altura), interpolation=cv2.INTER_LINEAR)


def redimensionar(bgra: np.ndarray, largura: int, altura: int) -> np.ndarray:
    """Devolve SEMPRE um buffer novo e gravável (ver `_reduzir`)."""
    return _reduzir(bgra, largura, altura)


def encaixar(bgra: np.ndarray, largura: int, altura: int) -> np.ndarray:
    """Como redimensionar, mas sem distorcer: se a proporção mudou (ex.: a janela capturada foi
    redimensionada no meio da transmissão), centraliza e completa com faixas pretas."""
    altura_origem, largura_origem = bgra.shape[:2]
    if (largura_origem, altura_origem) == (largura, altura):
        return bgra.copy()
    fator = min(largura / largura_origem, altura / altura_origem)
    novo = (max(1, round(largura_origem * fator)), max(1, round(altura_origem * fator)))
    if abs(novo[0] - largura) <= 2 and abs(novo[1] - altura) <= 2:  # mesma proporção
        return _reduzir(bgra, largura, altura)
    saida = np.zeros((altura, largura, 4), dtype=np.uint8)
    saida[..., 3] = 255
    x, y = (largura - novo[0]) // 2, (altura - novo[1]) // 2
    saida[y:y + novo[1], x:x + novo[0]] = _reduzir(bgra, *novo)
    return saida
