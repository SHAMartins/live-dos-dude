"""Desenha o cursor do Windows no frame. Nem a DXGI nem o GDI incluem o cursor na captura.

A imagem do cursor é obtida desenhando-o (DrawIconEx) duas vezes, sobre fundo preto e
sobre fundo branco:
  - pixel igual nos dois fundos  -> opaco, com essa cor;
  - pixel que segue o fundo      -> transparente (ou semitransparente, pela diferença);
  - pixel que INVERTE o fundo    -> cursor "XOR" (o I-beam padrão do Windows): inverte o frame.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD),
                ("bmBitsPixel", wintypes.WORD), ("bmBits", ctypes.c_void_p)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


_CURSOR_SHOWING = 0x1
_DI_NORMAL = 0x3

if sys.platform == "win32":
    # Assinaturas explícitas: sem restype, handles de 64 bits voltariam truncados como int de 32.
    def _assinar(funcao, restype, *argtypes) -> None:
        funcao.restype, funcao.argtypes = restype, argtypes

    _assinar(_user32.GetCursorInfo, wintypes.BOOL, ctypes.POINTER(_CURSORINFO))
    _assinar(_user32.GetIconInfo, wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ICONINFO))
    _assinar(_user32.GetDC, wintypes.HDC, wintypes.HWND)
    _assinar(_user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
    _assinar(_user32.DrawIconEx, wintypes.BOOL, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HANDLE,
             ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH, wintypes.UINT)
    _assinar(_gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
    _assinar(_gdi32.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, ctypes.POINTER(_BITMAPINFOHEADER),
             wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    _assinar(_gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
    _assinar(_gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
    _assinar(_gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
    _assinar(_gdi32.GetObjectW, ctypes.c_int, wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p)
    _assinar(_gdi32.GdiFlush, wintypes.BOOL)


@dataclass(frozen=True)
class ImagemCursor:
    cor: np.ndarray       # (h, w, 3) uint8, BGR já multiplicado pelo alfa
    alfa: np.ndarray      # (h, w) float32 em [0, 1]
    inverte: np.ndarray   # (h, w) bool: pixels que invertem o que está embaixo
    hotspot: tuple[int, int]


def separar_camadas(sobre_preto: np.ndarray, sobre_branco: np.ndarray, hotspot: tuple[int, int]) -> ImagemCursor:
    """Recupera cor, alfa e inversão a partir das duas renderizações (BGR uint8)."""
    preto = sobre_preto.astype(np.int16)
    branco = sobre_branco.astype(np.int16)
    diferenca = (branco - preto).max(axis=-1)          # 255 = transparente, 0 = opaco, <0 = inverte
    inverte = (preto > branco).all(axis=-1)
    alfa = np.clip(255 - diferenca, 0, 255).astype(np.float32) / 255
    alfa[inverte] = 0
    cor = np.where(inverte[..., None], 0, preto).astype(np.uint8)
    return ImagemCursor(cor, alfa, inverte, hotspot)


def aplicar(frame: np.ndarray, imagem: ImagemCursor, x: int, y: int) -> None:
    """Compõe o cursor no frame BGRA (in-place), com o canto superior esquerdo em (x, y). Recorta nas bordas."""
    h, w = imagem.alfa.shape
    altura, largura = frame.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, largura), min(y + h, altura)
    if x0 >= x1 or y0 >= y1:
        return
    cx, cy = x0 - x, y0 - y
    regiao = frame[y0:y1, x0:x1, :3]
    alfa = imagem.alfa[cy:cy + (y1 - y0), cx:cx + (x1 - x0), None]
    cor = imagem.cor[cy:cy + (y1 - y0), cx:cx + (x1 - x0)]
    inverte = imagem.inverte[cy:cy + (y1 - y0), cx:cx + (x1 - x0)]
    composto = cor + regiao * (1 - alfa)
    composto[inverte] = 255 - regiao[inverte]
    # clip: o arredondamento do DrawIconEx pode passar de 255 e o uint8 daria a volta (pontos escuros).
    regiao[:] = np.clip(composto, 0, 255).astype(np.uint8)


class DesenhistaCursor:
    """Lê a posição/forma atual do cursor e desenha no frame. Guarda em cache cada forma já vista."""

    def __init__(self) -> None:
        self._cache: dict[tuple[int, float], ImagemCursor | None] = {}
        self._desativado = sys.platform != "win32"

    def desenhar(self, frame: np.ndarray, origem: tuple[int, int], escala: float) -> None:
        """origem: canto do monitor capturado na área de trabalho; escala: saída / tela."""
        if self._desativado:
            return
        try:
            info = _CURSORINFO(cbSize=ctypes.sizeof(_CURSORINFO))
            if not _user32.GetCursorInfo(ctypes.byref(info)) or not info.flags & _CURSOR_SHOWING or not info.hCursor:
                return
            chave = (info.hCursor, round(escala, 3))
            if chave not in self._cache:
                self._cache[chave] = self._renderizar(info.hCursor, escala)
            imagem = self._cache[chave]
            if imagem is None:
                return
            x = round((info.ptScreenPos.x - origem[0]) * escala) - imagem.hotspot[0]
            y = round((info.ptScreenPos.y - origem[1]) * escala) - imagem.hotspot[1]
            aplicar(frame, imagem, x, y)
        except Exception:  # noqa: BLE001 - cursor é cosmético: nunca derruba a transmissão
            log.exception("falha ao desenhar o cursor; desativando")
            self._desativado = True

    def _renderizar(self, hcursor: int, escala: float) -> ImagemCursor | None:
        info = _ICONINFO()
        if not _user32.GetIconInfo(wintypes.HANDLE(hcursor), ctypes.byref(info)):
            return None
        try:
            bitmap = _BITMAP()
            _gdi32.GetObjectW(info.hbmMask, ctypes.sizeof(_BITMAP), ctypes.byref(bitmap))
            largura = bitmap.bmWidth
            # Cursor monocromático: a máscara tem altura dupla (AND em cima, XOR embaixo).
            altura = bitmap.bmHeight if info.hbmColor else bitmap.bmHeight // 2
            hotspot = (info.xHotspot, info.yHotspot)
        finally:
            for handle in (info.hbmMask, info.hbmColor):
                if handle:
                    _gdi32.DeleteObject(handle)

        sobre_preto = self._desenhar_sobre(hcursor, largura, altura, 0x00)
        sobre_branco = self._desenhar_sobre(hcursor, largura, altura, 0xFF)
        imagem = separar_camadas(sobre_preto, sobre_branco, hotspot)
        if escala == 1.0:
            return imagem
        return _redimensionar_cursor(imagem, escala)

    @staticmethod
    def _desenhar_sobre(hcursor: int, largura: int, altura: int, fundo: int) -> np.ndarray:
        tela = _user32.GetDC(None)
        dc = _gdi32.CreateCompatibleDC(tela)
        cabecalho = _BITMAPINFOHEADER(biSize=ctypes.sizeof(_BITMAPINFOHEADER), biWidth=largura,
                                      biHeight=-altura, biPlanes=1, biBitCount=32)  # altura negativa: de cima para baixo
        bits = ctypes.c_void_p()
        dib = _gdi32.CreateDIBSection(dc, ctypes.byref(cabecalho), 0, ctypes.byref(bits), None, 0)
        anterior = _gdi32.SelectObject(dc, dib)
        try:
            ctypes.memset(bits, fundo, largura * altura * 4)
            _user32.DrawIconEx(dc, 0, 0, wintypes.HANDLE(hcursor), largura, altura, 0, None, _DI_NORMAL)
            _gdi32.GdiFlush()
            buffer = (ctypes.c_uint8 * (largura * altura * 4)).from_address(bits.value)
            return np.frombuffer(buffer, dtype=np.uint8).reshape(altura, largura, 4)[..., :3].copy()
        finally:
            _gdi32.SelectObject(dc, anterior)
            _gdi32.DeleteObject(dib)
            _gdi32.DeleteDC(dc)
            _user32.ReleaseDC(None, tela)


def _redimensionar_cursor(imagem: ImagemCursor, escala: float) -> ImagemCursor:
    h, w = imagem.alfa.shape
    tamanho = (max(1, round(w * escala)), max(1, round(h * escala)))

    def reduzir(arr: np.ndarray, modo: str) -> np.ndarray:
        return np.asarray(Image.fromarray(arr, modo).resize(tamanho, Image.Resampling.BILINEAR))

    cor = reduzir(imagem.cor, "RGB")
    alfa = reduzir((imagem.alfa * 255).astype(np.uint8), "L").astype(np.float32) / 255
    inverte = reduzir(imagem.inverte.astype(np.uint8) * 255, "L") > 127
    hotspot = (round(imagem.hotspot[0] * escala), round(imagem.hotspot[1] * escala))
    return ImagemCursor(cor, alfa, inverte, hotspot)


def ativar_dpi_awareness() -> None:
    """Coordenadas em pixels físicos (senão, com escala de 125%/150%, o cursor sai deslocado)."""
    if sys.platform != "win32":
        return
    try:
        _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
