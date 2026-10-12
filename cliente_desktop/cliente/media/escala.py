from __future__ import annotations

import cv2
import numpy as np

# Medido (1080p -> 720p, BGRA): Pillow BILINEAR = 40 ms de CPU por frame; OpenCV INTER_AREA = 13 ms.
# Com 12 threads (padrão) o OpenCV gasta 19 ms de CPU espalhados pelos núcleos que o encoder
# precisa; 2 threads seguram o tempo de relógio para caber 60 fps sem roubar CPU à toa.
cv2.setNumThreads(2)


def tamanho_de_saida(largura_tela: int, altura_tela: int, largura_alvo: int, altura_alvo: int) -> tuple[int, int]:
    """Encaixa a tela na caixa pedida mantendo a proporção. Nunca aumenta; dimensões pares (exigência do I420).

    Ex.: 1920x1200 em 720p dá 1152x720, sem achatar a imagem.
    """
    fator = min(largura_alvo / largura_tela, altura_alvo / altura_tela, 1.0)
    largura = max(2, round(largura_tela * fator / 2) * 2)
    altura = max(2, round(altura_tela * fator / 2) * 2)
    return largura, altura


def redimensionar(bgra: np.ndarray, largura: int, altura: int) -> np.ndarray:
    """Devolve SEMPRE um buffer novo e gravável: o de entrada pode ser memória interna da
    captura, e o LiveKit só aceita buffers graváveis sem copiar.

    INTER_AREA (média por área) é o que mantém texto legível ao reduzir.
    """
    altura_origem, largura_origem = bgra.shape[:2]
    if (largura_origem, altura_origem) == (largura, altura):
        return bgra.copy()
    return cv2.resize(bgra, (largura, altura), interpolation=cv2.INTER_AREA)


def encaixar(bgra: np.ndarray, largura: int, altura: int) -> np.ndarray:
    """Como redimensionar, mas sem distorcer: se a proporção mudou (ex.: a janela capturada foi
    redimensionada no meio da transmissão), centraliza e completa com faixas pretas."""
    altura_origem, largura_origem = bgra.shape[:2]
    if (largura_origem, altura_origem) == (largura, altura):
        return bgra.copy()
    fator = min(largura / largura_origem, altura / altura_origem)
    novo = (max(1, round(largura_origem * fator)), max(1, round(altura_origem * fator)))
    interpolacao = cv2.INTER_AREA if fator < 1 else cv2.INTER_LINEAR
    if abs(novo[0] - largura) <= 2 and abs(novo[1] - altura) <= 2:  # mesma proporção
        return cv2.resize(bgra, (largura, altura), interpolation=interpolacao)
    saida = np.zeros((altura, largura, 4), dtype=np.uint8)
    saida[..., 3] = 255
    x, y = (largura - novo[0]) // 2, (altura - novo[1]) // 2
    saida[y:y + novo[1], x:x + novo[0]] = cv2.resize(bgra, novo, interpolation=interpolacao)
    return saida
