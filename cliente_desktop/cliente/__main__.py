"""Ponto de entrada: três threads com papéis fixos.

  principal  -> janela (Tk exige a thread principal)
  "asyncio"  -> WebSocket do backend + sala do LiveKit (controlador)
  "midia"    -> captura, redimensionamento e entrega dos quadros ao encoder
  (+ "bandeja", do pystray)

    cd cliente_desktop && python -m cliente
"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import sys
from logging.handlers import RotatingFileHandler

from cliente import VERSAO, sistema
from cliente.config import PASTA_DADOS, ConfigCliente
from cliente.controlador import Controlador
from cliente.credenciais import CredenciaisWindows
from cliente.estado import PainelEstado
from cliente.media.captura import ThreadMidia
from cliente.media.cursor import ativar_dpi_awareness
from cliente.media.publicador import Publicador  # importa o livekit já na inicialização
from cliente.ponte import ThreadAsyncio
from cliente.ui.bandeja import Bandeja
from cliente.ui.janela import AcoesUI, Janela

log = logging.getLogger("cliente")
_mutex = None  # mantido vivo enquanto o processo roda


def _configurar_log() -> None:
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    arquivo = RotatingFileHandler(PASTA_DADOS / "cliente.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handlers: list[logging.Handler] = [arquivo]
    if sys.stderr is not None:  # com pythonw não há console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s")


def _instancia_unica() -> bool:
    """Duas instâncias com o mesmo token se derrubariam no backend (fechamento 4002)."""
    global _mutex
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _mutex = kernel32.CreateMutexW(None, False, "Local\\LiveDosDudeCliente")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _diagnostico() -> int:
    """LiveDosDude.exe --diagnostico: confere se o executável carregou tudo (sem abrir janela).

    O .exe não tem console, então o relatório vai para %APPDATA%/LiveDosDude/diagnostico.txt.
    """
    linhas, falhas = [f"Live dos Dude {VERSAO} | empacotado={sistema.empacotado()} | Python {sys.version.split()[0]}"], 0

    def checar(nome: str, funcao) -> None:
        nonlocal falhas
        try:
            linhas.append(f"[ok]    {nome}: {funcao()}")
        except Exception as erro:  # noqa: BLE001 - o relatório lista tudo que falhou
            falhas += 1
            linhas.append(f"[FALHA] {nome}: {type(erro).__name__}: {erro}")

    def livekit_ffi() -> str:
        from livekit import rtc
        rtc.VideoSource(64, 64)  # carrega a biblioteca nativa do LiveKit
        return "biblioteca nativa carregada"

    def tk() -> str:
        import customtkinter
        raiz = customtkinter.CTk()
        versao = raiz.tk.call("info", "patchlevel")
        raiz.destroy()
        return f"Tk {versao}"

    def keyring_backend() -> str:
        import keyring
        backend = keyring.get_keyring()
        if "fail" in type(backend).__module__:
            raise RuntimeError(f"sem cofre de senhas utilizável ({backend})")
        return type(backend).__name__

    def telas() -> str:
        from cliente.media import fontes
        return ", ".join(f"{t.largura}x{t.altura}" for t in fontes.listar_telas())

    def dxgi() -> str:
        from cliente.media.captura import CapturaDXGI
        fonte = CapturaDXGI(None)
        quadro = fonte.capturar()
        fonte.fechar()
        return f"captura {quadro.shape[1]}x{quadro.shape[0]}"

    def janelas() -> str:
        from cliente.media import fontes
        import windows_capture  # noqa: F401 - só confere que a extensão nativa carrega
        return f"{len(fontes.listar_janelas())} janelas visíveis; windows_capture ok"

    def opencv() -> str:
        import cv2
        return cv2.__version__

    def bandeja() -> str:
        import pystray
        return pystray.Icon.__module__

    def som() -> str:
        import os
        import time
        from concurrent.futures import ThreadPoolExecutor
        from cliente.media import audio

        def capturar() -> str:
            # Thread própria, como no app: o COM da thread principal é do dxcam/comtypes.
            audio._ole32.CoInitializeEx(None, 0)
            discord = audio.processo_discord()
            sessao, dados = audio._Sessao(discord or os.getpid()), bytearray()
            try:
                inicio = time.perf_counter()
                while time.perf_counter() - inicio < 0.3:
                    sessao.ler(dados, 10)
            finally:
                sessao.fechar()
            excluido = f"sem o Discord (pid {discord})" if discord else "Discord fechado"
            return f"loopback por processo ok, {len(dados) // audio.BYTES_POR_AMOSTRA} amostras em 0,3 s, {excluido}"

        with ThreadPoolExecutor(1) as executor:
            return executor.submit(capturar).result(timeout=10)

    for nome, funcao in (("LiveKit", livekit_ffi), ("Interface (Tk)", tk), ("Cofre de credenciais", keyring_backend),
                         ("Telas", telas), ("Captura DXGI", dxgi), ("Captura de janela", janelas),
                         ("OpenCV", opencv), ("Bandeja", bandeja), ("Som do PC", som)):
        checar(nome, funcao)
    linhas.append("RESULTADO: " + ("OK" if not falhas else f"{falhas} FALHA(S)"))
    relatorio = "\n".join(linhas)
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    (PASTA_DADOS / "diagnostico.txt").write_text(relatorio + "\n", encoding="utf-8")
    if sys.stdout is not None:
        print(relatorio)
    return 1 if falhas else 0


def _limpar_dados() -> int:
    """LiveDosDude.exe --limpar-dados: apaga o pareamento (token no Gerenciador de Credenciais).

    Chamado pelo desinstalador quando o usuário pede para remover também os dados; a pasta
    %APPDATA%/LiveDosDude o próprio desinstalador apaga depois.
    """
    from cliente.config import BACKEND_PADRAO
    from cliente.credenciais import CredenciaisWindows

    credenciais = CredenciaisWindows()
    for url in {ConfigCliente.carregar().backend_url, BACKEND_PADRAO}:
        credenciais.apagar(url)
    return 0


def main() -> None:
    if "--limpar-dados" in sys.argv:
        sys.exit(_limpar_dados())
    if "--diagnostico" in sys.argv:
        ativar_dpi_awareness()
        codigo = _diagnostico()
        if sys.stdout is not None:
            sys.stdout.flush()
        # Sai direto, sem a limpeza do interpretador (que só atrasa). O comtypes do dxcam às vezes ainda
        # imprime um "access violation" ao soltar objetos COM; não afeta o relatório nem o código de saída.
        os._exit(codigo)
    _configurar_log()
    sistema.identificar_na_barra_de_tarefas()
    if not _instancia_unica():
        ctypes.windll.user32.MessageBoxW(None, "O Live dos Dude já está aberto. Procure o ícone na bandeja.",
                                         "Live dos Dude", 0x40)
        return
    ativar_dpi_awareness()
    log.info("cliente desktop %s iniciando", VERSAO)

    config = ConfigCliente.carregar()
    credenciais = CredenciaisWindows()
    painel = PainelEstado()
    ponte = ThreadAsyncio()
    midia = ThreadMidia()
    controlador = Controlador(config, credenciais, Publicador(midia, config.sacrificar), painel.publicar)
    ponte.iniciar()
    ponte.submeter(controlador.executar())

    pedidos: queue.Queue[str] = queue.Queue()
    bandeja = Bandeja(ao_abrir=lambda: pedidos.put("abrir"),
                      ao_parar=lambda: ponte.submeter(controlador.parar_local()),
                      ao_sair=lambda: pedidos.put("sair"))

    def salvar(url: str, nome: str) -> None:
        config.backend_url, config.nome_dispositivo = url, nome
        config.salvar()
        ponte.chamar(controlador.trocar_destino, url, nome)

    janela: Janela | None = None

    def sair() -> None:
        if janela is not None:
            janela.quit()

    acoes = AcoesUI(
        parear=lambda codigo: ponte.chamar(controlador.parear, codigo),
        parar=lambda: ponte.submeter(controlador.parar_local()),
        reconectar=lambda: ponte.chamar(controlador.reconectar),
        salvar=salvar,
        salvar_config=config.salvar,
        aplicar_agora=lambda: ponte.submeter(controlador.aplicar_configuracao()),
        sair=sair,
    )
    janela = Janela(painel, acoes, config, pedidos, lambda: (midia.fps_real, midia.ms_por_frame, midia.aviso), bandeja,
                    iniciar_oculta=credenciais.ler(config.backend_url) is not None)
    bandeja.iniciar()
    try:
        janela.mainloop()
    finally:
        log.info("encerrando")
        try:
            ponte.submeter(controlador.encerrar()).result(timeout=10)
        except Exception:  # noqa: BLE001 - encerrar tem que seguir mesmo com erro
            log.exception("erro ao encerrar o controlador")
        midia.fechar()
        bandeja.parar()
        ponte.parar()
        janela.destroy()


if __name__ == "__main__":
    main()
