"""Som do PC para a transmissão, SEM o Discord: WASAPI "process loopback" numa thread própria.

Captura tudo o que os programas tocam, menos a árvore de processos do Discord (as vozes da call e os
sons de notificação dele), como o Go Live do próprio Discord faz. Usa a API de loopback por processo
do Windows (ActivateAudioInterfaceAsync em VAD\\Process_Loopback), que existe a partir do Windows 10
2004; em Windows mais antigo a transmissão segue sem som (melhor que vazar a call).

O Discord é procurado a cada 2 s: se ele abrir, fechar ou reiniciar no meio da transmissão, a captura
é refeita excluindo o processo novo (sem Discord aberto, exclui o próprio app, que não toca nada).
Discord aberto no navegador não tem como ser separado do resto do navegador.

Uma thread permanente, com o COM do Windows (MTA) inicializado nela.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import queue
import threading
import time
import uuid
from ctypes import wintypes as wt

import numpy as np
from livekit import rtc

log = logging.getLogger(__name__)

TAXA = 48000
CANAIS = 2
AMOSTRAS_POR_QUADRO = 480  # 10 ms, o tamanho de quadro que o WebRTC usa
BYTES_POR_AMOSTRA = 2 * CANAIS  # PCM 16 bits intercalado
EXECUTAVEIS_DISCORD = {"discord.exe", "discordptb.exe", "discordcanary.exe", "discorddevelopment.exe"}
INTERVALO_BUSCA_DISCORD = 2.0
SILENCIO_APOS = 0.05  # sem pacotes por 50 ms = nada tocando: completa com zeros para o áudio não "pular"


# --- Win32 / COM à mão (ctypes) ---------------------------------------------------

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _guid(texto: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(texto).bytes_le)


IID_IUNKNOWN = _guid("00000000-0000-0000-C000-000000000046")
IID_IAGILEOBJECT = _guid("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_COMPLETION_HANDLER = _guid("41D949AB-9862-444A-80F6-C261334DA5EB")
IID_IAUDIOCLIENT = _guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAUDIOCAPTURECLIENT = _guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317")

VT_BLOB = 65
ATIVACAO_LOOPBACK_DE_PROCESSO = 1
MODO_EXCLUIR_ARVORE = 1
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
E_NOINTERFACE = 0x80004002 - 2**32


class _Blob(ctypes.Structure):
    _fields_ = [("cbSize", wt.ULONG), ("pBlobData", ctypes.c_void_p)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wt.USHORT), ("r1", wt.WORD), ("r2", wt.WORD), ("r3", wt.WORD), ("blob", _Blob)]


class _ParametrosAtivacao(ctypes.Structure):  # AUDIOCLIENT_ACTIVATION_PARAMS com o PROCESS_LOOPBACK_PARAMS
    _fields_ = [("ActivationType", ctypes.c_int), ("TargetProcessId", wt.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", wt.WORD), ("nChannels", wt.WORD), ("nSamplesPerSec", wt.DWORD),
                ("nAvgBytesPerSec", wt.DWORD), ("nBlockAlign", wt.WORD), ("wBitsPerSample", wt.WORD),
                ("cbSize", wt.WORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


_ole32 = ctypes.WinDLL("ole32")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_mmdevapi = ctypes.WinDLL("Mmdevapi")

_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wt.DWORD]
_ole32.CoInitializeEx.restype = ctypes.c_long
_mmdevapi.ActivateAudioInterfaceAsync.argtypes = [wt.LPCWSTR, ctypes.POINTER(GUID), ctypes.POINTER(PROPVARIANT),
                                                  ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_mmdevapi.ActivateAudioInterfaceAsync.restype = ctypes.c_long
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
_kernel32.CreateEventW.restype = wt.HANDLE
_kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]


def _ok(hr: int, oque: str) -> None:
    if hr < 0:
        raise OSError(f"{oque} falhou (0x{hr & 0xFFFFFFFF:08X})")


def _metodo(objeto: int, indice: int, *argtypes):
    """O método nº `indice` da vtable de um objeto COM, pronto para chamar com (objeto, *args)."""
    vtable = ctypes.cast(ctypes.c_void_p(objeto), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[indice])


def _soltar(objeto: int | None) -> None:
    if objeto:
        _metodo(objeto, 2)(objeto)  # IUnknown::Release


_QI = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
_REF = ctypes.WINFUNCTYPE(wt.ULONG, ctypes.c_void_p)
_COMPLETOU = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)


class _Avisador:
    """IActivateAudioInterfaceCompletionHandler (ágil) feito à mão: só avisa que a ativação terminou.

    Vive enquanto o módulo viver (o Windows pode segurar a referência depois do aviso), por isso
    AddRef/Release não contam nada.
    """

    _ACEITAS = {bytes(IID_IUNKNOWN), bytes(IID_IAGILEOBJECT), bytes(IID_COMPLETION_HANDLER)}

    def __init__(self) -> None:
        self.terminou = threading.Event()
        self._funcoes = (_QI(self._consultar), _REF(lambda _: 1), _REF(lambda _: 1), _COMPLETOU(self._completou))
        self._vtable = (ctypes.c_void_p * 4)(*(ctypes.cast(f, ctypes.c_void_p) for f in self._funcoes))
        self._objeto = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.ponteiro = ctypes.addressof(self._objeto)

    def _consultar(self, este, iid, saida) -> int:
        if bytes(iid.contents) in self._ACEITAS:
            saida[0] = este
            return 0
        saida[0] = None
        return E_NOINTERFACE

    def _completou(self, este, operacao) -> int:
        self.terminou.set()
        return 0


_avisador: _Avisador | None = None


def processo_discord() -> int | None:
    """PID do processo raiz do Discord aberto (o pai dos outros Discord.exe), ou None."""
    foto = _kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not foto or foto == wt.HANDLE(-1).value:
        return None
    pais: dict[int, int] = {}
    try:
        entrada = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        sucesso = _kernel32.Process32FirstW(foto, ctypes.byref(entrada))
        while sucesso:
            if entrada.szExeFile.lower() in EXECUTAVEIS_DISCORD:
                pais[entrada.th32ProcessID] = entrada.th32ParentProcessID
            sucesso = _kernel32.Process32NextW(foto, ctypes.byref(entrada))
    finally:
        _kernel32.CloseHandle(foto)
    raizes = sorted(pid for pid, pai in pais.items() if pai not in pais)
    return raizes[0] if raizes else None


class _Sessao:
    """Um IAudioClient de loopback que capta tudo menos a árvore de processos `pid_excluido`."""

    def __init__(self, pid_excluido: int) -> None:
        self.cliente = self.captura = None
        self.evento = _kernel32.CreateEventW(None, False, False, None)
        try:
            self.cliente = self._ativar(pid_excluido)
            formato = WAVEFORMATEX(1, CANAIS, TAXA, TAXA * BYTES_POR_AMOSTRA, BYTES_POR_AMOSTRA, 16, 0)  # PCM
            _ok(_metodo(self.cliente, 3, ctypes.c_int, wt.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                        ctypes.POINTER(WAVEFORMATEX), ctypes.c_void_p)(
                self.cliente, 0, AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK
                | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM, 1_000_000, 0, ctypes.byref(formato), None),
                "IAudioClient::Initialize")  # buffer de 100 ms: folga para pausas do Python
            _ok(_metodo(self.cliente, 13, wt.HANDLE)(self.cliente, self.evento), "SetEventHandle")
            captura = ctypes.c_void_p()
            _ok(_metodo(self.cliente, 14, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                self.cliente, ctypes.byref(IID_IAUDIOCAPTURECLIENT), ctypes.byref(captura)), "GetService")
            self.captura = captura.value
            self._tamanho_pacote = _metodo(self.captura, 5, ctypes.POINTER(ctypes.c_uint32))
            self._pegar = _metodo(self.captura, 3, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
                                  ctypes.POINTER(wt.DWORD), ctypes.c_void_p, ctypes.c_void_p)
            self._devolver = _metodo(self.captura, 4, ctypes.c_uint32)
            _ok(_metodo(self.cliente, 10)(self.cliente), "IAudioClient::Start")
        except BaseException:
            self.fechar()
            raise

    @staticmethod
    def _ativar(pid_excluido: int) -> int:
        global _avisador
        if _avisador is None:
            _avisador = _Avisador()
        _avisador.terminou.clear()
        parametros = _ParametrosAtivacao(ATIVACAO_LOOPBACK_DE_PROCESSO, pid_excluido, MODO_EXCLUIR_ARVORE)
        variante = PROPVARIANT(vt=VT_BLOB)
        variante.blob.cbSize = ctypes.sizeof(parametros)
        variante.blob.pBlobData = ctypes.addressof(parametros)
        operacao = ctypes.c_void_p()
        _ok(_mmdevapi.ActivateAudioInterfaceAsync("VAD\\Process_Loopback", ctypes.byref(IID_IAUDIOCLIENT),
                                                  ctypes.byref(variante), _avisador.ponteiro, ctypes.byref(operacao)),
            "ActivateAudioInterfaceAsync (requer Windows 10 2004 ou mais novo)")
        try:
            if not _avisador.terminou.wait(5):
                raise TimeoutError("o Windows não respondeu à ativação do áudio")
            resultado, cliente = ctypes.c_long(), ctypes.c_void_p()
            _ok(_metodo(operacao.value, 3, ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_void_p))(
                operacao.value, ctypes.byref(resultado), ctypes.byref(cliente)), "GetActivateResult")
            _ok(resultado.value, "ativar a captura de som por processo")
            return cliente.value
        finally:
            _soltar(operacao.value)

    def ler(self, destino: bytearray, espera_ms: int) -> bool:
        """Junta em `destino` o que chegou (esperando até `espera_ms`). True se veio algum pacote."""
        _kernel32.WaitForSingleObject(self.evento, espera_ms)
        veio = False
        tamanho, dados, quadros, flags = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_uint32(), wt.DWORD()
        while True:
            _ok(self._tamanho_pacote(self.captura, ctypes.byref(tamanho)), "GetNextPacketSize")
            if not tamanho.value:
                return veio
            _ok(self._pegar(self.captura, ctypes.byref(dados), ctypes.byref(quadros), ctypes.byref(flags), None, None),
                "GetBuffer")
            n = quadros.value * BYTES_POR_AMOSTRA
            destino += bytes(n) if flags.value & AUDCLNT_BUFFERFLAGS_SILENT else ctypes.string_at(dados, n)
            self._devolver(self.captura, quadros.value)
            veio = True

    def fechar(self) -> None:
        if self.cliente:
            _metodo(self.cliente, 11)(self.cliente)  # Stop
        _soltar(self.captura)
        _soltar(self.cliente)
        self.cliente = self.captura = None
        if self.evento:
            _kernel32.CloseHandle(self.evento)
            self.evento = None


# --- a thread de captura ----------------------------------------------------------

class CapturaAudio:
    def __init__(self) -> None:
        self._pedidos: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._parar = threading.Event()
        self._pronta = threading.Event()
        self._ociosa = threading.Event()
        self._ociosa.set()
        self.erro = ""
        self.nivel = 0.0              # RMS do último quadro (0 a 1), para a UI
        self.excluindo: int | None = None  # PID do Discord deixado de fora (None = Discord fechado)

    def iniciar(self, destino: rtc.AudioSource, loop: asyncio.AbstractEventLoop) -> None:
        """Começa a capturar. Espera até 5 s pela abertura da captura; falha vira RuntimeError."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._trabalhar, name="audio", daemon=True)
            self._thread.start()
        self.parar()
        self._parar.clear()
        self._pronta.clear()
        self.erro = ""
        self._pedidos.put((destino, loop))
        self._pronta.wait(6)
        if self.erro:
            raise RuntimeError(self.erro)

    def parar(self) -> None:
        """Bloqueante (até ~1 quadro): chame via asyncio.to_thread a partir do event loop."""
        self._parar.set()
        self._ociosa.wait(2)
        self.nivel = 0.0

    def fechar(self) -> None:
        self.parar()
        if self._thread is not None:
            self._pedidos.put(None)
            self._thread.join(2)
            self._thread = None

    # --- dentro da thread de áudio ----------------------------------------------

    def _trabalhar(self) -> None:
        _ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED: a ativação responde de outra thread
        while (pedido := self._pedidos.get()) is not None:
            destino, loop = pedido
            self._ociosa.clear()
            try:
                self._capturar(destino, loop)
            finally:
                self._ociosa.set()

    def _abrir(self) -> _Sessao:
        self.excluindo = processo_discord()
        sessao = _Sessao(self.excluindo or os.getpid())
        log.info("capturando o som do PC %s", f"sem o Discord (pid {self.excluindo})" if self.excluindo
                 else "(Discord fechado)")
        return sessao

    def _capturar(self, destino: rtc.AudioSource, loop: asyncio.AbstractEventLoop) -> None:
        try:
            sessao = self._abrir()
        except Exception as erro:  # noqa: BLE001 - Windows antigo, serviço de áudio parado...
            self.erro = f"não foi possível capturar o som do PC: {erro}"
            log.warning(self.erro)
            return
        finally:
            self._pronta.set()

        pendente = bytearray()
        quadro_bytes = AMOSTRAS_POR_QUADRO * BYTES_POR_AMOSTRA
        ultimo_pacote = relogio_silencio = time.perf_counter()
        proxima_busca = ultimo_pacote + INTERVALO_BUSCA_DISCORD
        try:
            while not self._parar.is_set():
                agora = time.perf_counter()
                if agora >= proxima_busca:
                    proxima_busca = agora + INTERVALO_BUSCA_DISCORD
                    if processo_discord() != self.excluindo:  # Discord abriu, fechou ou reiniciou
                        sessao.fechar()
                        sessao = self._abrir()
                try:
                    veio = sessao.ler(pendente, 10)
                except OSError as erro:  # dispositivo sumiu etc.: tenta reabrir em 1 s
                    log.warning("captura de som interrompida (%s); reabrindo", erro)
                    sessao.fechar()
                    self._parar.wait(1)
                    sessao = self._abrir()
                    continue
                agora = time.perf_counter()
                if veio:
                    ultimo_pacote = relogio_silencio = agora
                elif agora - ultimo_pacote > SILENCIO_APOS:
                    # Nada tocando: o Windows não entrega pacotes. Zeros no ritmo do relógio mantêm
                    # o áudio contínuo (inclusive os 50 ms até perceber o silêncio).
                    while relogio_silencio + 0.01 <= agora:
                        pendente += bytes(quadro_bytes)
                        relogio_silencio += 0.01
                while len(pendente) >= quadro_bytes:
                    self._enviar(bytes(pendente[:quadro_bytes]), destino, loop)
                    del pendente[:quadro_bytes]
        except Exception as erro:  # noqa: BLE001 - não derruba o app; a transmissão segue sem som
            log.warning("captura de som encerrada: %s", erro)
        finally:
            sessao.fechar()

    def _enviar(self, pcm: bytes, destino: rtc.AudioSource, loop: asyncio.AbstractEventLoop) -> None:
        amostras = np.frombuffer(pcm, np.int16)
        self.nivel = float(np.sqrt(np.mean(amostras.astype(np.float32) ** 2))) / 32768.0
        quadro = rtc.AudioFrame(pcm, TAXA, CANAIS, AMOSTRAS_POR_QUADRO)
        futuro = asyncio.run_coroutine_threadsafe(destino.capture_frame(quadro), loop)
        try:
            futuro.result(timeout=0.5)  # contrapressão: se o encoder atrasar, a captura espera
        except Exception:  # noqa: BLE001 - loop parando ou fila cheia: descarta este quadro
            futuro.cancel()
