"""Escala, cursor e a thread de captura (esta última captura a tela de verdade)."""

from __future__ import annotations

import ctypes
import sys
import time

import numpy as np
import pytest

from cliente.media.cursor import DesenhistaCursor, ImagemCursor, aplicar, ativar_dpi_awareness, separar_camadas
from cliente.media.escala import redimensionar, tamanho_de_saida

so_windows = pytest.mark.skipif(sys.platform != "win32", reason="captura e cursor são do Windows")


@pytest.mark.parametrize(("tela", "alvo", "esperado"), [
    ((1920, 1080), (1280, 720), (1280, 720)),
    ((1920, 1200), (1280, 720), (1152, 720)),    # 16:10 não é achatado
    ((2560, 1080), (1920, 1080), (1920, 810)),   # ultrawide
    ((1366, 768), (1280, 720), (1280, 720)),     # arredonda para par sem distorcer
    ((1280, 720), (1920, 1080), (1280, 720)),    # nunca aumenta
    ((3840, 2160), (854, 480), (854, 480)),
])
def test_tamanho_de_saida(tela, alvo, esperado):
    largura, altura = tamanho_de_saida(*tela, *alvo)
    assert (largura, altura) == esperado
    assert largura % 2 == 0 and altura % 2 == 0


def test_redimensionar_devolve_buffer_novo_gravavel_e_preserva_canais():
    origem = np.zeros((1080, 1920, 4), np.uint8)
    origem[..., 0], origem[..., 2] = 200, 10  # B alto, R baixo
    for largura, altura in ((1920, 1080), (1280, 720)):
        saida = redimensionar(origem, largura, altura)
        assert saida.shape == (altura, largura, 4)
        assert saida.flags.writeable and saida.flags.c_contiguous
        assert not np.shares_memory(saida, origem)
        assert tuple(saida[100, 100, :3]) == (200, 0, 10)


def test_reducao_forte_passa_pela_metade_e_mantem_qualidade():
    # 1080p -> 480p (2,25x) e 4K -> 720p: o resultado tem o tamanho exato e é um buffer novo e gravável.
    rng = np.random.default_rng(1)
    for origem_wh, alvo in (((1920, 1080), (854, 480)), ((3840, 2160), (1280, 720)), ((1920, 1080), (640, 360))):
        origem = rng.integers(0, 255, (origem_wh[1], origem_wh[0], 4), dtype=np.uint8)
        saida = redimensionar(origem, *alvo)
        assert saida.shape == (alvo[1], alvo[0], 4)
        assert saida.flags.writeable and saida.flags.c_contiguous and not np.shares_memory(saida, origem)


def test_reducao_2_para_1_e_media_de_2x2_sem_serrilhado():
    import cv2

    xadrez = np.zeros((1080, 1920, 4), np.uint8)
    xadrez[::2, ::2] = 255
    xadrez[1::2, 1::2] = 255                    # pior caso para serrilhado: padrão de 1 pixel
    saida = redimensionar(xadrez, 960, 540)
    assert 120 <= int(saida[..., 0].min()) and int(saida[..., 0].max()) <= 135   # ~cinza médio, sem listras
    # E, em escala não inteira, fica perto do INTER_AREA (referência de qualidade): > 40 dB em conteúdo suave.
    base = cv2.GaussianBlur(np.random.default_rng(2).integers(0, 255, (1080, 1920, 4), dtype=np.uint8), (0, 0), 3)
    ref = cv2.resize(base, (1280, 720), interpolation=cv2.INTER_AREA).astype(float)
    mse = np.mean((redimensionar(base, 1280, 720).astype(float) - ref) ** 2)
    assert 10 * np.log10(255 ** 2 / max(mse, 1e-9)) > 40


def test_separar_camadas_reconhece_opaco_transparente_e_invertido():
    sobre_preto = np.array([[[10, 20, 30], [0, 0, 0], [255, 255, 255]]], np.uint8)
    sobre_branco = np.array([[[10, 20, 30], [255, 255, 255], [0, 0, 0]]], np.uint8)
    imagem = separar_camadas(sobre_preto, sobre_branco, (0, 0))
    assert imagem.alfa.tolist() == [[1.0, 0.0, 0.0]]
    assert imagem.inverte.tolist() == [[False, False, True]]
    assert imagem.cor[0, 0].tolist() == [10, 20, 30]


def test_aplicar_compoe_inverte_e_recorta_nas_bordas():
    cor = np.array([[[255] * 3, [128] * 3], [[0] * 3, [0] * 3]], np.uint8)  # pré-multiplicada pelo alfa
    imagem = ImagemCursor(
        cor=cor, alfa=np.array([[1.0, 0.5], [0.0, 0.0]], np.float32),
        inverte=np.array([[False, False], [True, False]]), hotspot=(0, 0),
    )
    frame = np.full((4, 4, 4), 100, np.uint8)
    aplicar(frame, imagem, 1, 1)
    assert frame[1, 1, 0] == 255          # opaco
    assert frame[1, 2, 0] == 128 + 50     # meio transparente: cor pré-multiplicada + 100 * 0.5
    assert frame[2, 1, 0] == 155          # invertido: 255 - 100
    assert frame[2, 2, 0] == 100          # transparente
    assert frame[1, 1, 3] == 100          # o alfa do frame não é tocado

    aplicar(frame, imagem, 3, 3)          # metade fora: não estoura
    aplicar(frame, imagem, -5, -5)        # inteiro fora: ignora


def test_aplicar_satura_em_vez_de_dar_a_volta_no_uint8():
    """Arredondamento do Windows pode deixar cor > 255 * alfa; sem clip, 305 vira 49."""
    imagem = ImagemCursor(cor=np.full((1, 1, 3), 255, np.uint8), alfa=np.full((1, 1), 0.5, np.float32),
                          inverte=np.zeros((1, 1), bool), hotspot=(0, 0))
    frame = np.full((1, 1, 4), 100, np.uint8)
    aplicar(frame, imagem, 0, 0)
    assert frame[0, 0, 0] == 255


@so_windows
@pytest.mark.tela
def test_renderiza_cursores_reais_do_windows():
    ativar_dpi_awareness()
    user32 = ctypes.WinDLL("user32")
    user32.LoadCursorW.restype = ctypes.c_void_p
    user32.LoadCursorW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    desenhista = DesenhistaCursor()
    for nome, idc in (("seta", 32512), ("texto", 32513), ("mão", 32649)):
        imagem = desenhista._renderizar(user32.LoadCursorW(None, ctypes.c_void_p(idc)), 1.0)
        visiveis = (imagem.alfa > 0.5).sum() + imagem.inverte.sum()
        assert visiveis > 20, f"cursor {nome} saiu vazio"
        reduzida = desenhista._renderizar(user32.LoadCursorW(None, ctypes.c_void_p(idc)), 0.5)
        assert reduzida.alfa.shape[0] == round(imagem.alfa.shape[0] * 0.5)


class _DestinoFalso:
    def __init__(self) -> None:
        self.quadros: list[tuple[int, int]] = []

    def capture_frame(self, quadro) -> None:
        self.quadros.append((quadro.width, quadro.height))


@so_windows
@pytest.mark.tela
@pytest.mark.parametrize("preferida", ["dxgi", "gdi"])
def test_thread_de_midia_captura_no_ritmo_e_reinicia(preferida):
    from cliente.media.captura import ThreadMidia

    midia = ThreadMidia(preferida=preferida)
    try:
        largura_tela, altura_tela, nome = midia.preparar().result(timeout=10)
        assert nome == preferida
        largura, altura = tamanho_de_saida(largura_tela, altura_tela, 1280, 720)
        for _ in range(2):  # duas transmissões seguidas com a mesma fonte
            destino, falhas = _DestinoFalso(), []
            midia.iniciar(destino, largura, altura, 20, ao_falhar=falhas.append)
            time.sleep(1.0)
            inicio = time.perf_counter()
            midia.parar()
            assert time.perf_counter() - inicio < 0.5  # parar não espera mais que ~1 quadro
            assert 15 <= len(destino.quadros) <= 22, len(destino.quadros)
            assert set(destino.quadros) == {(largura, altura)}
            assert not falhas
    finally:
        midia.fechar()


def test_encaixar_mantem_proporcao_com_faixas_pretas():
    from cliente.media.escala import encaixar

    branco = np.full((1000, 1000, 4), 255, np.uint8)  # janela quadrada num vídeo 16:9
    saida = encaixar(branco, 1280, 720)
    assert saida.shape == (720, 1280, 4) and saida.flags.writeable
    assert saida[360, 640, 0] == 255            # conteúdo no centro
    assert saida[360, 10, 0] == 0               # faixa preta na lateral
    assert (saida[..., 3] == 255).all()          # alfa opaco inclusive nas faixas
    mesma_proporcao = encaixar(np.zeros((1080, 1920, 4), np.uint8), 1280, 720)
    assert mesma_proporcao.shape == (720, 1280, 4)


def test_descrever_fontes():
    from cliente.media.fontes import descrever

    assert descrever(None) == "Tela principal"
    assert descrever({"tipo": "tela", "largura": 2560, "altura": 1440, "principal": True}) == "Tela 2560x1440 (principal)"
    assert descrever({"tipo": "janela", "titulo": "Jogo", "processo": "jogo.exe"}) == "Jogo (jogo.exe)"


def _janela_de_teste():
    from cliente.media import fontes

    candidatas = [j for j in fontes.listar_janelas() if not fontes.janela_minimizada(j.hwnd) and j.largura >= 400]
    if not candidatas:
        pytest.skip("nenhuma janela aberta e visível para capturar")
    return max(candidatas, key=lambda j: j.largura * j.altura)


@so_windows
@pytest.mark.tela
def test_lista_telas_e_janelas_com_miniaturas():
    from cliente.media import fontes

    telas = fontes.listar_telas()
    assert telas and any(t.principal for t in telas)
    miniatura = fontes.miniatura_tela(telas[0])
    assert miniatura is not None and miniatura.shape[1] == fontes.LARGURA_MINIATURA and miniatura.shape[2] == 3

    janela = _janela_de_teste()
    assert janela.processo.lower().endswith(".exe")
    assert all(j.processo != "python.exe" or j.hwnd != janela.hwnd for j in fontes.listar_janelas())
    assert fontes.localizar_janela(janela.como_fonte()) == janela.hwnd
    # Mesmo programa com título diferente (aba trocada): ainda reencontra.
    assert fontes.localizar_janela({**janela.como_fonte(), "hwnd": 0, "titulo": "outro título"}) is not None
    assert fontes.localizar_janela({"tipo": "janela", "processo": "nao_existe.exe", "titulo": "x"}) is None


@so_windows
@pytest.mark.tela
def test_thread_de_midia_troca_entre_tela_e_janela():
    from cliente.media.captura import FalhaCaptura, ThreadMidia
    from cliente.media.fontes import listar_telas

    janela = _janela_de_teste()
    midia = ThreadMidia()
    try:
        for fonte, captura_esperada in ((listar_telas()[0].como_fonte(), "dxgi"), (janela.como_fonte(), "janela"),
                                        (None, "dxgi")):
            largura_fonte, altura_fonte, nome = midia.preparar(fonte).result(timeout=10)
            assert nome == captura_esperada
            largura, altura = tamanho_de_saida(largura_fonte, altura_fonte, 1280, 720)
            destino, falhas = _DestinoFalso(), []
            midia.iniciar(destino, largura, altura, 20, ao_falhar=falhas.append)
            time.sleep(1.0)
            midia.parar()
            assert len(destino.quadros) >= 12, (nome, len(destino.quadros))
            assert set(destino.quadros) == {(largura, altura)} and not falhas

        with pytest.raises(FalhaCaptura, match="não está aberta"):
            midia.preparar({"tipo": "janela", "processo": "nao_existe.exe", "titulo": "x"}).result(timeout=10)
    finally:
        midia.fechar()
