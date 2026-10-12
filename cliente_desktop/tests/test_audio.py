"""Som do PC por loopback de processo (captura de verdade, com um tom tocado por outro processo)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="loopback de processo é do Windows")

TOCAR_TOM = textwrap.dedent("""
    import math, struct, sys, time, wave, winsound
    with wave.open(sys.argv[1], "wb") as arquivo:
        arquivo.setnchannels(1); arquivo.setsampwidth(2); arquivo.setframerate(48000)
        arquivo.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 48000)))
                                     for i in range(48000)))
    winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
    time.sleep(30)
""")


@pytest.fixture(scope="module")
def tocando_tom(tmp_path_factory):
    """Um processo à parte tocando 440 Hz em loop (como o Discord tocaria as vozes)."""
    pasta = tmp_path_factory.mktemp("tom")
    script = pasta / "tom.py"
    script.write_text(TOCAR_TOM, encoding="utf-8")
    processo = subprocess.Popen([sys.executable, str(script), str(pasta / "tom.wav")])
    time.sleep(1.5)
    yield processo
    processo.kill()


def _energia_440(pid_excluido: int) -> float:
    """Captura ~1 s excluindo `pid_excluido` e devolve quanto o pico de 440 Hz se destaca do espectro."""
    from cliente.media import audio

    audio._ole32.CoInitializeEx(None, 0)
    sessao, dados = audio._Sessao(pid_excluido), bytearray()
    try:
        inicio = time.perf_counter()
        while time.perf_counter() - inicio < 1.2:
            sessao.ler(dados, 10)
    finally:
        sessao.fechar()
    canal = np.frombuffer(bytes(dados), np.int16).reshape(-1, 2)[:, 0].astype(np.float64)[-48000:]
    assert len(canal) >= 24000, "a captura entregou pouco áudio"
    espectro = np.abs(np.fft.rfft(canal))
    frequencias = np.fft.rfftfreq(len(canal), 1 / 48000)
    return espectro[(frequencias > 430) & (frequencias < 450)].max() / max(espectro.mean(), 1e-9)


def test_captura_o_som_dos_outros_processos(tocando_tom):
    assert _energia_440(os.getpid()) > 200


def test_deixa_de_fora_a_arvore_do_processo_excluido(tocando_tom):
    # É assim que as vozes do Discord ficam fora da transmissão.
    assert _energia_440(tocando_tom.pid) < 50


def test_processo_discord_aponta_para_a_raiz():
    from cliente.media.audio import EXECUTAVEIS_DISCORD, processo_discord

    pid = processo_discord()
    if pid is None:
        pytest.skip("Discord fechado")
    saida = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, check=True).stdout
    assert saida.split(",")[0].strip('"').lower() in EXECUTAVEIS_DISCORD
