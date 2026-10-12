"""Captura de tela ou de janela numa thread própria, alimentando o VideoSource do LiveKit no ritmo pedido.

Toda operação com a fonte de captura roda na MESMA thread (um ThreadPoolExecutor de 1
worker): os objetos COM da DXGI e os DCs do GDI não gostam de trocar de thread.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Protocol

import numpy as np
from livekit import rtc

from cliente.media import fontes
from cliente.media.cursor import DesenhistaCursor
from cliente.media.escala import encaixar

log = logging.getLogger(__name__)

LIMITE_FALHAS_S = 5.0  # captura falhando direto por mais que isso encerra a transmissão


class FalhaCaptura(Exception):
    pass


class FonteEncerrada(FalhaCaptura):
    """A fonte acabou de vez (janela fechada): encerra na hora, sem esperar o LIMITE_FALHAS_S."""


class FonteCaptura(Protocol):
    nome: str                 # "dxgi", "gdi" ou "janela"
    largura: int
    altura: int
    origem: tuple[int, int]   # canto do monitor na área de trabalho virtual (para o cursor)
    desenhar_cursor: bool     # False quando a própria captura já traz o cursor

    def capturar(self) -> np.ndarray | None:
        """Frame BGRA (altura, largura, 4). Pode ser memória interna: vale até a próxima chamada."""

    def fechar(self) -> None: ...


def _mesma_tela(tela: dict | None, esquerda: int, topo: int) -> bool:
    return tela is not None and (tela.get("esquerda"), tela.get("topo")) == (esquerda, topo)


class CapturaDXGI:
    """DXGI Desktop Duplication (dxcam): ~3 ms por frame em 1080p."""

    nome = "dxgi"
    desenhar_cursor = True

    def __init__(self, tela: dict | None) -> None:
        import dxcam

        self._camera = None
        if tela is not None:
            # A ordem da DXGI não é a mesma do Windows/mss: escolhe a saída pela posição na área de trabalho.
            for indice in range(8):
                try:
                    camera = dxcam.create(output_idx=indice, output_color="BGRA", processor_backend="numpy")
                except Exception:  # noqa: BLE001 - acabaram as saídas
                    break
                area = camera._output.desc.DesktopCoordinates  # sem API pública para a posição
                if _mesma_tela(tela, area.left, area.top):
                    self._camera = camera
                    break
                camera.release()
        if self._camera is None:
            self._camera = dxcam.create(output_color="BGRA", processor_backend="numpy")  # principal
        self.largura, self.altura = self._camera.width, self._camera.height
        try:
            area = self._camera._output.desc.DesktopCoordinates
            self.origem = (area.left, area.top)
        except AttributeError:
            self.origem = (0, 0)

    def capturar(self) -> np.ndarray | None:
        # new_frame_only=False: com a tela parada, repete o último frame. Sem isso o encoder
        # fica sem entrada e quem entra na sala vê tela preta até algo mudar.
        return self._camera.grab(copy=False, new_frame_only=False)

    def fechar(self) -> None:
        self._camera.release()


class CapturaGDI:
    """GDI (mss): ~20 ms por frame em 1080p. Reserva para quando a DXGI não está disponível
    (Área de Trabalho Remota, alguns drivers)."""

    nome = "gdi"
    desenhar_cursor = True

    def __init__(self, tela: dict | None) -> None:
        import mss

        self._sct = mss.MSS()
        monitores = self._sct.monitors[1:]
        escolhido = next((m for m in monitores if _mesma_tela(tela, m["left"], m["top"])), None)
        self._monitor = escolhido or next((m for m in monitores if m.get("is_primary")), monitores[0])
        self.largura, self.altura = self._monitor["width"], self._monitor["height"]
        self.origem = (self._monitor["left"], self._monitor["top"])

    def capturar(self) -> np.ndarray | None:
        tela = self._sct.grab(self._monitor)
        return np.frombuffer(tela.raw, dtype=np.uint8).reshape(tela.height, tela.width, 4)

    def fechar(self) -> None:
        self._sct.close()


class CapturaJanela:
    """Uma janela específica, mesmo coberta por outras (Windows Graphics Capture, como o Discord).

    No Windows 10 a API desenha uma borda amarela em volta da janela capturada, só na tela
    de quem transmite; não dá para desligar (o Windows 11 permite).
    """

    nome = "janela"
    desenhar_cursor = False  # a Graphics Capture já inclui o cursor
    origem = (0, 0)

    def __init__(self, hwnd: int, titulo: str = "") -> None:
        from windows_capture import WindowsCapture

        self.hwnd, self.titulo = hwnd, titulo
        self.intervalo_minimo = 1 / 60  # ajustado pela ThreadMidia para o fps da transmissão
        self._trava = threading.Lock()
        self._quadro: np.ndarray | None = None
        self._ultima_copia = 0.0
        self._fechada = False
        self._primeiro = threading.Event()

        captura = WindowsCapture(cursor_capture=True, window_hwnd=hwnd)

        @captura.event
        def on_frame_arrived(frame, _controle) -> None:
            agora = time.perf_counter()
            # A API entrega até ~120 quadros/s; copiar só no ritmo da transmissão poupa CPU.
            if agora - self._ultima_copia < self.intervalo_minimo * 0.9:
                return
            self._ultima_copia = agora
            copia = frame.frame_buffer.copy()  # o buffer só vale dentro deste callback
            with self._trava:
                self._quadro = copia
            self._primeiro.set()

        @captura.event
        def on_closed() -> None:
            self._fechada = True
            self._primeiro.set()

        try:
            self._controle = captura.start_free_threaded()
        except Exception as erro:  # noqa: BLE001 - hwnd inválido, janela sem suporte...
            raise FalhaCaptura(f"não foi possível capturar a janela: {erro}") from erro
        if not self._primeiro.wait(3):
            self.fechar()
            raise FalhaCaptura("a janela não mandou imagem: ela está minimizada? Restaure e tente de novo")
        if self._fechada:
            raise FonteEncerrada("a janela foi fechada")
        self.altura, self.largura = self._quadro.shape[:2]

    def capturar(self) -> np.ndarray | None:
        if self._fechada:
            raise FonteEncerrada(f"a janela {self.titulo!r} foi fechada" if self.titulo else "a janela foi fechada")
        with self._trava:
            return self._quadro

    def fechar(self) -> None:
        try:
            self._controle.stop()
            self._controle.wait()
        except Exception:  # noqa: BLE001 - já pode ter parado sozinha (janela fechada)
            pass


def abrir_fonte(fonte: dict | None, preferida: str = "dxgi") -> FonteCaptura:
    if fonte and fonte.get("tipo") == "janela":
        hwnd = fontes.localizar_janela(fonte)
        if hwnd is None:
            raise FalhaCaptura(f"a janela {fontes.descrever(fonte)} não está aberta")
        return CapturaJanela(hwnd, fonte.get("titulo", ""))
    if preferida == "dxgi" and sys.platform == "win32":
        try:
            return CapturaDXGI(fonte)
        except Exception as erro:  # noqa: BLE001 - qualquer falha da DXGI cai para o GDI
            log.warning("DXGI indisponível (%s); usando GDI", erro)
    return CapturaGDI(fonte)


def _chave(fonte: dict | None) -> str:
    """Identidade da fonte: decide se dá para reaproveitar a que já está aberta."""
    if not fonte or fonte.get("tipo") != "janela":
        return json.dumps({"tela": [fonte.get("esquerda"), fonte.get("topo")] if fonte else None})
    return json.dumps({"janela": [fonte.get("processo"), fonte.get("titulo"), fonte.get("hwnd")]})


class ThreadMidia:
    """Dona da fonte de captura. Abre sob demanda, reaproveita entre transmissões se for a mesma, fecha no fim."""

    def __init__(self, preferida: str = "dxgi") -> None:
        self._preferida = preferida
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="midia")
        self._fonte: FonteCaptura | None = None
        self._chave_fonte: str | None = None
        self._cursor = DesenhistaCursor()
        self._parar = threading.Event()
        self._laco: concurrent.futures.Future | None = None
        # Estatísticas e avisos, lidos pela UI (atribuições simples, sem trava).
        self.fps_real = 0.0
        self.ms_por_frame = 0.0
        self.aviso = ""

    def preparar(self, fonte: dict | None = None) -> concurrent.futures.Future:
        """Abre a fonte pedida (ou reaproveita a aberta) e devolve (largura, altura, nome)."""
        return self._executor.submit(self._preparar, fonte)

    def iniciar(self, destino: rtc.VideoSource, largura: int, altura: int, fps: int,
                ao_falhar: Callable[[str], None]) -> None:
        if self._laco is not None and not self._laco.done():
            raise RuntimeError("captura já está rodando")
        self._parar.clear()
        self._laco = self._executor.submit(self._capturar_em_laco, destino, largura, altura, fps)
        self._laco.add_done_callback(lambda f: self._ao_terminar(f, ao_falhar))

    def parar(self, timeout: float = 5.0) -> None:
        """Bloqueante: chame via asyncio.to_thread a partir do event loop."""
        self._parar.set()
        if self._laco is not None:
            try:
                self._laco.result(timeout)
            except (FalhaCaptura, concurrent.futures.CancelledError):
                pass
            self._laco = None
        self.fps_real = 0.0
        self.aviso = ""

    def fechar(self) -> None:
        self.parar()
        self._executor.submit(self._fechar_fonte).result(timeout=5)
        self._executor.shutdown(wait=True)

    # --- dentro da thread de mídia ---------------------------------------------

    def _preparar(self, fonte: dict | None) -> tuple[int, int, str]:
        chave = _chave(fonte)
        janela_fechada = isinstance(self._fonte, CapturaJanela) and self._fonte._fechada
        if self._fonte is None or chave != self._chave_fonte or janela_fechada:
            self._fechar_fonte()
            self._fonte = abrir_fonte(fonte, self._preferida)
            self._chave_fonte = chave
            log.info("captura %s aberta: %dx%d (%s)", self._fonte.nome, self._fonte.largura, self._fonte.altura,
                     fontes.descrever(fonte))
        return self._fonte.largura, self._fonte.altura, self._fonte.nome

    def _fechar_fonte(self) -> None:
        if self._fonte is not None:
            self._fonte.fechar()
            self._fonte, self._chave_fonte = None, None

    def _capturar_em_laco(self, destino: rtc.VideoSource, largura: int, altura: int, fps: int) -> None:
        fonte = self._fonte
        assert fonte is not None, "chame preparar() antes de iniciar()"
        if isinstance(fonte, CapturaJanela):
            fonte.intervalo_minimo = 1 / fps
        escala = largura / fonte.largura
        intervalo = 1 / fps
        proximo = time.perf_counter()
        falhando_desde: float | None = None
        contagem, inicio_janela = 0, time.perf_counter()

        while not self._parar.is_set():
            inicio = time.perf_counter()
            try:
                bgra = fonte.capturar()
                falhando_desde = None
            except FonteEncerrada:
                raise
            except Exception as erro:  # noqa: BLE001 - ex.: tela de bloqueio/UAC (área de trabalho segura)
                falhando_desde = falhando_desde or inicio
                if inicio - falhando_desde > LIMITE_FALHAS_S:
                    raise FalhaCaptura(f"captura falhando há mais de {LIMITE_FALHAS_S:.0f} s: {erro}") from erro
                bgra = None

            if bgra is not None:
                saida = encaixar(bgra, largura, altura)
                if fonte.desenhar_cursor:
                    self._cursor.desenhar(saida, fonte.origem, escala)
                # Array 1D: o VideoFrame usa o buffer direto em vez de copiar.
                quadro = rtc.VideoFrame(largura, altura, rtc.VideoBufferType.BGRA, memoryview(saida.reshape(-1)))
                destino.capture_frame(quadro)
                contagem += 1
                self.ms_por_frame = 0.9 * self.ms_por_frame + 0.1 * (time.perf_counter() - inicio) * 1000

            agora = time.perf_counter()
            if agora - inicio_janela >= 1.0:
                self.fps_real = contagem / (agora - inicio_janela)
                contagem, inicio_janela = 0, agora
                minimizada = isinstance(fonte, CapturaJanela) and fontes.janela_minimizada(fonte.hwnd)
                self.aviso = "A janela está minimizada: o vídeo fica congelado até você restaurá-la." if minimizada else ""

            # time.sleep tem resolução de ~1 ms no Windows (Python 3.11+); Event.wait não.
            proximo += intervalo
            espera = proximo - time.perf_counter()
            if espera > 0:
                time.sleep(espera)
            else:
                proximo = time.perf_counter()  # atrasou: descarta o atraso em vez de acumular

    def _ao_terminar(self, futuro: concurrent.futures.Future, ao_falhar: Callable[[str], None]) -> None:
        if futuro.cancelled():
            return
        erro = futuro.exception()
        if erro is not None:
            log.error("captura encerrada por erro: %s", erro)
            ao_falhar(str(erro))
