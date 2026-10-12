"""O que pode ser transmitido: telas e janelas abertas, com miniatura (o seletor estilo "Go Live").

A fonte escolhida é salva no config.json como dict:
  {"tipo": "tela", "esquerda": 0, "topo": 0, "largura": 1920, "altura": 1080, "nome": "..."}
  {"tipo": "janela", "hwnd": 459880, "titulo": "...", "processo": "Discord.exe"}
O hwnd muda quando o app reinicia, então a janela é reencontrada pelo processo + título.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import asdict, dataclass

import cv2
import numpy as np

LARGURA_MINIATURA = 320


@dataclass(frozen=True)
class Tela:
    indice: int
    nome: str
    esquerda: int
    topo: int
    largura: int
    altura: int
    principal: bool

    def como_fonte(self) -> dict:
        return {"tipo": "tela", **{k: v for k, v in asdict(self).items() if k != "indice"}}


@dataclass(frozen=True)
class Janela:
    hwnd: int
    titulo: str
    processo: str
    largura: int
    altura: int

    def como_fonte(self) -> dict:
        return {"tipo": "janela", "hwnd": self.hwnd, "titulo": self.titulo, "processo": self.processo}


def descrever(fonte: dict | None) -> str:
    if not fonte or fonte.get("tipo") == "tela":
        if fonte and fonte.get("largura"):
            return f"Tela {fonte['largura']}x{fonte['altura']}" + (" (principal)" if fonte.get("principal") else "")
        return "Tela principal"
    return f"{fonte.get('titulo', 'Janela')} ({fonte.get('processo', '?')})"


# --- telas ----------------------------------------------------------------------

def listar_telas() -> list[Tela]:
    import mss

    with mss.MSS() as sct:
        return [
            Tela(i, m.get("name") or f"Tela {i + 1}", m["left"], m["top"], m["width"], m["height"],
                 bool(m.get("is_primary")))
            for i, m in enumerate(sct.monitors[1:])
        ]


def miniatura_tela(tela: Tela) -> np.ndarray | None:
    """RGB pequeno, para o seletor."""
    import mss

    try:
        with mss.MSS() as sct:
            shot = sct.grab({"left": tela.esquerda, "top": tela.topo, "width": tela.largura, "height": tela.altura})
        bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return _reduzir(bgra)
    except Exception:  # noqa: BLE001 - miniatura é opcional
        return None


# --- janelas --------------------------------------------------------------------

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi")
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _assinar(funcao, restype, *argtypes) -> None:
        funcao.restype, funcao.argtypes = restype, argtypes

    _ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _assinar(_user32.EnumWindows, wintypes.BOOL, _ENUM_PROC, wintypes.LPARAM)
    _assinar(_user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
    _assinar(_user32.IsWindow, wintypes.BOOL, wintypes.HWND)
    _assinar(_user32.IsIconic, wintypes.BOOL, wintypes.HWND)
    _assinar(_user32.GetWindow, wintypes.HWND, wintypes.HWND, wintypes.UINT)
    _assinar(_user32.GetWindowTextLengthW, ctypes.c_int, wintypes.HWND)
    _assinar(_user32.GetWindowTextW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _assinar(_user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    _assinar(_user32.GetWindowLongW, wintypes.LONG, wintypes.HWND, ctypes.c_int)
    _assinar(_user32.GetWindowThreadProcessId, wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _assinar(_user32.PrintWindow, wintypes.BOOL, wintypes.HWND, wintypes.HDC, wintypes.UINT)
    _assinar(_user32.GetDC, wintypes.HDC, wintypes.HWND)
    _assinar(_user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
    _assinar(_gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
    _assinar(_gdi32.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
             ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    _assinar(_gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
    _assinar(_gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
    _assinar(_gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
    _assinar(_gdi32.GdiFlush, wintypes.BOOL)
    _assinar(_dwmapi.DwmGetWindowAttribute, ctypes.c_long, wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    _assinar(_kernel32.OpenProcess, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _assinar(_kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
    _assinar(_kernel32.QueryFullProcessImageNameW, wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
             wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))

_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PW_RENDERFULLCONTENT = 0x2  # pega também janelas desenhadas por GPU (navegador, jogos)
_IGNORAR = {"Program Manager"}


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _titulo(hwnd: int) -> str:
    n = _user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buffer, n + 1)
    return buffer.value


def _processo(hwnd: int) -> tuple[int, str]:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return pid.value, "?"
    try:
        tamanho = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(tamanho.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(tamanho)):
            return pid.value, os.path.basename(buffer.value)
        return pid.value, "?"
    finally:
        _kernel32.CloseHandle(handle)


def _tamanho(hwnd: int) -> tuple[int, int]:
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def _capturavel(hwnd: int) -> bool:
    """Janelas "de verdade": visíveis, sem dono, não ocultas pelo DWM, que não sejam de ferramenta."""
    if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, _GW_OWNER):
        return False
    if _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
        return False
    oculta = ctypes.c_int(0)
    _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(oculta), ctypes.sizeof(oculta))
    return not oculta.value


def listar_janelas() -> list[Janela]:
    if sys.platform != "win32":
        return []
    meu_pid = os.getpid()
    achadas: list[Janela] = []

    @_ENUM_PROC
    def visitar(hwnd, _lparam):
        if _capturavel(hwnd):
            titulo = _titulo(hwnd)
            largura, altura = _tamanho(hwnd)
            if titulo and titulo not in _IGNORAR and (largura >= 120 or _user32.IsIconic(hwnd)):
                pid, processo = _processo(hwnd)
                if pid != meu_pid:
                    achadas.append(Janela(int(hwnd), titulo, processo, largura, altura))
        return True

    _user32.EnumWindows(visitar, 0)
    return achadas


def janela_existe(hwnd: int) -> bool:
    return sys.platform == "win32" and bool(_user32.IsWindow(hwnd))


def janela_minimizada(hwnd: int) -> bool:
    return sys.platform == "win32" and bool(_user32.IsIconic(hwnd))


def localizar_janela(fonte: dict) -> int | None:
    """Reencontra a janela salva: o mesmo hwnd, se ainda for do mesmo programa; senão processo + título."""
    janelas = listar_janelas()
    processo, titulo = fonte.get("processo"), fonte.get("titulo")
    for j in janelas:
        if j.hwnd == fonte.get("hwnd") and j.processo == processo:
            return j.hwnd
    mesmo_processo = [j for j in janelas if j.processo == processo]
    for j in mesmo_processo:
        if j.titulo == titulo:
            return j.hwnd
    # Título muda (navegador troca de aba, Discord troca de canal): fica com a maior janela do programa.
    if mesmo_processo:
        return max(mesmo_processo, key=lambda j: j.largura * j.altura).hwnd
    return None


def miniatura_janela(janela: Janela) -> np.ndarray | None:
    """RGB pequeno via PrintWindow. Janela minimizada não tem o que desenhar: devolve None."""
    if sys.platform != "win32" or _user32.IsIconic(janela.hwnd):
        return None
    largura, altura = _tamanho(janela.hwnd)
    if largura <= 0 or altura <= 0:
        return None
    tela = _user32.GetDC(None)
    dc = _gdi32.CreateCompatibleDC(tela)
    cabecalho = _BITMAPINFOHEADER(biSize=ctypes.sizeof(_BITMAPINFOHEADER), biWidth=largura,
                                  biHeight=-altura, biPlanes=1, biBitCount=32)
    bits = ctypes.c_void_p()
    dib = _gdi32.CreateDIBSection(dc, ctypes.byref(cabecalho), 0, ctypes.byref(bits), None, 0)
    anterior = _gdi32.SelectObject(dc, dib)
    try:
        if not _user32.PrintWindow(janela.hwnd, dc, _PW_RENDERFULLCONTENT):
            return None
        _gdi32.GdiFlush()
        buffer = (ctypes.c_uint8 * (largura * altura * 4)).from_address(bits.value)
        bgra = np.frombuffer(buffer, dtype=np.uint8).reshape(altura, largura, 4).copy()
    finally:
        _gdi32.SelectObject(dc, anterior)
        _gdi32.DeleteObject(dib)
        _gdi32.DeleteDC(dc)
        _user32.ReleaseDC(None, tela)
    if bgra[..., :3].max() == 0:  # algumas janelas não aceitam PrintWindow e saem pretas
        return None
    return _reduzir(bgra)


def _reduzir(bgra: np.ndarray) -> np.ndarray:
    altura, largura = bgra.shape[:2]
    nova_altura = max(1, round(altura * LARGURA_MINIATURA / largura))
    pequena = cv2.resize(bgra, (LARGURA_MINIATURA, nova_altura), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(pequena, cv2.COLOR_BGRA2RGB)
