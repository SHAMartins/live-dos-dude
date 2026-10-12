"""Gera o executável do cliente e o .zip para distribuir.

    cd cliente_desktop
    .venv/Scripts/python empacotamento/empacotar.py

Saída: dist/LiveDosDude/LiveDosDude.exe, dist/LiveDosDude-<versão>-windows.zip e, se o Inno Setup 6
estiver instalado, dist/LiveDosDude-<versão>-instalador.exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

PASTA = Path(__file__).resolve().parent
RAIZ = PASTA.parent
sys.path.insert(0, str(RAIZ))

from cliente import VERSAO  # noqa: E402
from cliente.estado import Estado  # noqa: E402
from cliente.ui.aparencia import icone  # noqa: E402


def gerar_icone() -> None:
    """O ícone vermelho de "ao vivo", em todos os tamanhos que o Windows usa."""
    imagem = icone(Estado.TRANSMITINDO, 256)
    imagem.save(PASTA / "icone.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])


def gerar_versao() -> None:
    """Metadados que aparecem em Propriedades → Detalhes do LiveDosDude.exe."""
    partes = (VERSAO.split(".") + ["0", "0", "0"])[:4]
    tupla = ", ".join(str(int(p)) for p in partes)
    (PASTA / "versao.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tupla}), prodvers=({tupla})),
  kids=[
    StringFileInfo([StringTable('041604b0', [
      StringStruct('ProductName', 'Live dos Dude'),
      StringStruct('FileDescription', 'Live dos Dude: transmissão de tela pelo Discord'),
      StringStruct('FileVersion', '{VERSAO}'),
      StringStruct('ProductVersion', '{VERSAO}'),
      StringStruct('OriginalFilename', 'LiveDosDude.exe'),
      StringStruct('InternalName', 'LiveDosDude')])]),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])
  ]
)
""", encoding="utf-8")


def localizar_iscc() -> Path | None:
    """O compilador do Inno Setup: no PATH ou nos lugares onde o instalador dele costuma ficar."""
    if achado := shutil.which("ISCC"):
        return Path(achado)
    candidatos = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"]
    candidatos += [Path(os.environ[v]) / "Inno Setup 6" / "ISCC.exe" for v in ("ProgramFiles(x86)", "ProgramFiles")
                   if v in os.environ]
    return next((c for c in candidatos if c.exists()), None)


def gerar_instalador(dist: Path) -> Path | None:
    iscc = localizar_iscc()
    if iscc is None:
        print("\nInno Setup 6 não encontrado: pulei o instalador (winget install --id JRSoftware.InnoSetup -e).")
        return None
    subprocess.run([str(iscc), f"/DVersaoApp={VERSAO}", "/Q", str(PASTA / "instalador.iss")], check=True)
    return dist / f"LiveDosDude-{VERSAO}-instalador.exe"


def tamanho(caminho: Path) -> str:
    total = sum(f.stat().st_size for f in caminho.rglob("*") if f.is_file()) if caminho.is_dir() else caminho.stat().st_size
    return f"{total / 1024 / 1024:.0f} MB"


def main() -> None:
    inicio = time.perf_counter()
    gerar_icone()
    gerar_versao()
    dist, build = RAIZ / "dist", RAIZ / "build"
    subprocess.run([sys.executable, "-m", "PyInstaller", str(PASTA / "LiveDosDude.spec"),
                    "--noconfirm", "--clean", "--distpath", str(dist), "--workpath", str(build)],
                   check=True, cwd=RAIZ)
    pasta_app = dist / "LiveDosDude"
    pacote = dist / f"LiveDosDude-{VERSAO}-windows.zip"
    pacote.unlink(missing_ok=True)
    with zipfile.ZipFile(pacote, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zipado:
        for arquivo in sorted(pasta_app.rglob("*")):
            if arquivo.is_file():
                zipado.write(arquivo, Path("LiveDosDude") / arquivo.relative_to(pasta_app))
    shutil.rmtree(build, ignore_errors=True)
    instalador = gerar_instalador(dist)
    print(f"\npasta:      {pasta_app} ({tamanho(pasta_app)})")
    print(f"zip:        {pacote} ({tamanho(pacote)})")
    if instalador:
        print(f"instalador: {instalador} ({tamanho(instalador)})")
    print(f"pronto em {time.perf_counter() - inicio:.0f} s")


if __name__ == "__main__":
    main()
