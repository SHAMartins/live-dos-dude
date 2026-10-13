"""Medidas do sistema para a telemetria: CPU total do PC (jogo incluso) e uso da GPU.

Tudo é "melhor esforço": se algo não estiver disponível, devolve None e a telemetria segue sem o campo.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes as wt

log = logging.getLogger(__name__)


class CpuDoSistema:
    """% de uso de todos os núcleos desde a chamada anterior (GetSystemTimes)."""

    def __init__(self) -> None:
        self._antes = self._ler()

    @staticmethod
    def _ler() -> tuple[int, int, int] | None:
        if sys.platform != "win32":
            return None
        ocioso, nucleo, usuario = wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(ocioso), ctypes.byref(nucleo), ctypes.byref(usuario)):
            return None
        valor = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime  # noqa: E731
        return valor(ocioso), valor(nucleo), valor(usuario)

    def percentual(self) -> float | None:
        agora, antes = self._ler(), self._antes
        self._antes = agora
        if agora is None or antes is None:
            return None
        ocioso = agora[0] - antes[0]
        total = (agora[1] - antes[1]) + (agora[2] - antes[2])  # o tempo de núcleo já inclui o ocioso
        return None if total <= 0 else max(0.0, min(100.0, (1 - ocioso / total) * 100))


class _Utilizacao(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memoria", ctypes.c_uint)]


class UsoDaGpu:
    """Uso da GPU pela NVML (vem com o driver da NVIDIA). Em outras placas, `percentual()` é sempre None."""

    def __init__(self) -> None:
        self._nvml = self._dispositivo = None
        if sys.platform != "win32":
            return
        for caminho in ("nvml.dll", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll"):
            try:
                nvml = ctypes.WinDLL(caminho)
                if nvml.nvmlInit_v2() != 0:
                    continue
                dispositivo = ctypes.c_void_p()
                if nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dispositivo)) != 0:
                    continue
                self._nvml, self._dispositivo = nvml, dispositivo
                return
            except OSError:
                continue

    def percentual(self) -> float | None:
        if self._nvml is None:
            return None
        uso = _Utilizacao()
        if self._nvml.nvmlDeviceGetUtilizationRates(self._dispositivo, ctypes.byref(uso)) != 0:
            return None
        return float(uso.gpu)
