# PyInstaller: gera dist/LiveDosDude/LiveDosDude.exe (pasta, não arquivo único: abre em ~1 s,
# em vez de descompactar ~200 MB a cada abertura). Rode pelo empacotar.py, não direto.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

PASTA = Path(SPECPATH)
RAIZ = PASTA.parent

datas, binaries, hiddenimports = [], [], []
# Pacotes com bibliotecas nativas e arquivos de dados carregados em tempo de execução.
for pacote in ("livekit", "windows_capture", "customtkinter", "dxcam"):
    d, b, h = collect_all(pacote)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += [
    "pystray._win32",            # o pystray escolhe o backend em tempo de execução
    "keyring.backends.Windows",  # o keyring acha os cofres por entry points
]
datas += [(str(PASTA / "icone.ico"), ".")]

a = Analysis(
    [str(PASTA / "iniciar.py")],
    pathex=[str(RAIZ)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "IPython", "matplotlib", "tkinter.test", "app"],
    noarchive=False,
)
# O OpenCV traz o FFmpeg (~25 MB) para ler vídeos; só usamos o resize.
a.binaries = [b for b in a.binaries if "opencv_videoio_ffmpeg" not in b[0]]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiveDosDude",
    console=False,                      # app de bandeja: sem janela de console
    icon=str(PASTA / "icone.ico"),
    version=str(PASTA / "versao.txt"),
    upx=False,                          # UPX costuma disparar falso positivo em antivírus
)
coll = COLLECT(exe, a.binaries, a.datas, name="LiveDosDude", upx=False)
