"""Medidas do sistema para a telemetria (leitura de verdade do Windows)."""

from __future__ import annotations

import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="GetSystemTimes/NVML são do Windows")


def test_cpu_do_sistema_fica_entre_0_e_100():
    from cliente.media.medidas import CpuDoSistema

    medidor = CpuDoSistema()
    fim = time.perf_counter() + 0.4
    while time.perf_counter() < fim:  # carga para o medidor ter o que ver
        pass
    valor = medidor.percentual()
    assert valor is not None and 0 <= valor <= 100


def test_gpu_nao_quebra_sem_placa_nvidia():
    from cliente.media.medidas import UsoDaGpu

    valor = UsoDaGpu().percentual()
    assert valor is None or 0 <= valor <= 100
