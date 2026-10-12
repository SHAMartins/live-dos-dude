"""Cliente desktop FALSO: fala o protocolo do backend sem capturar a tela.

Serve para testar o backend antes do cliente real existir (e é usado pelos testes):

    python tools/cliente_falso.py --parear ABC-234      # pareia com o código do /parear e fica ouvindo
    python tools/cliente_falso.py                       # reconecta com o token salvo
    python tools/cliente_falso.py --publicar            # ao receber 'iniciar', publica vídeo de teste no LiveKit
    python tools/cliente_falso.py --modo falhar         # responde 'iniciar' com erro
    python tools/cliente_falso.py --modo mudo           # nunca responde (simula PC travado)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Literal

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

try:
    # Importado já na inicialização: carregar a lib nativa leva centenas de ms e
    # travaria o event loop no meio do protocolo (o ack sairia atrasado).
    from livekit import rtc
except ImportError:  # só é necessário com --publicar
    rtc = None

log = logging.getLogger("cliente_falso")
VERSAO_PROTOCOLO = 1
ARQUIVO_TOKEN = Path(__file__).with_name(".token_cliente_falso")
Modo = Literal["normal", "falhar", "mudo"]


class ClienteFalso:
    def __init__(self, url: str, *, modo: Modo = "normal", publicar: bool = False) -> None:
        self.url = url
        self.modo = modo
        self.publicar = publicar
        self.token: str | None = None
        self.sessao_ativa: str | None = None
        self.recebidas: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.fechamento: int | None = None  # código WS com que o servidor fechou
        self._ws: ClientConnection | None = None
        self._loop_tarefa: asyncio.Task | None = None
        self._publicador: _PublicadorLiveKit | None = None

    async def conectar(self, *, codigo: str | None = None, token: str | None = None,
                       nome: str = "PC de teste") -> dict[str, Any]:
        """Faz o handshake e devolve a resposta (pareado, bem_vindo ou erro)."""
        self._ws = await connect(self.url)
        if codigo is not None:
            hello = {"tipo": "parear", "v": VERSAO_PROTOCOLO, "codigo": codigo, "nome_dispositivo": nome}
        else:
            hello = {"tipo": "hello", "v": VERSAO_PROTOCOLO, "device_token": token,
                     "versao_cliente": "falso-0.1", "sessao_ativa": self.sessao_ativa}
        await self._ws.send(json.dumps(hello))
        resposta = json.loads(await self._ws.recv())
        if resposta["tipo"] == "pareado":
            self.token = resposta["device_token"]
        elif resposta["tipo"] == "bem_vindo":
            self.token = token
        self._loop_tarefa = asyncio.create_task(self._loop())
        return resposta

    async def esperar(self, tipo: str, timeout: float = 5.0) -> dict[str, Any]:
        """Próxima mensagem recebida do tipo pedido (descarta as outras)."""
        async with asyncio.timeout(timeout):
            while True:
                mensagem = await self.recebidas.get()
                if mensagem["tipo"] == tipo:
                    return mensagem

    async def esperar_fechamento(self, timeout: float = 5.0) -> int | None:
        if self._loop_tarefa is not None:
            await asyncio.wait_for(asyncio.shield(self._loop_tarefa), timeout)
        return self.fechamento

    async def encerrar_pela_bandeja(self, motivo: str = "parado pela bandeja") -> None:
        """Simula o usuário parando a transmissão direto no PC."""
        if self.sessao_ativa:
            await self._parar_publicacao()
            await self._enviar({"tipo": "transmissao_encerrada", "sessao_id": self.sessao_ativa, "motivo": motivo})
            self.sessao_ativa = None

    async def enviar_bruto(self, dados: str) -> None:
        assert self._ws is not None
        await self._ws.send(dados)

    async def fechar(self) -> None:
        await self._parar_publicacao()
        if self._ws is not None:
            await self._ws.close()
        if self._loop_tarefa is not None:
            await asyncio.gather(self._loop_tarefa, return_exceptions=True)

    # --- internos -------------------------------------------------------------

    async def _loop(self) -> None:
        assert self._ws is not None
        try:
            async for bruto in self._ws:
                mensagem = json.loads(bruto)
                log.info("<- %s", _resumo(mensagem))
                await self.recebidas.put(mensagem)
                if mensagem["tipo"] == "iniciar":
                    await self._ao_iniciar(mensagem)
                elif mensagem["tipo"] == "parar":
                    await self._ao_parar(mensagem)
        except ConnectionClosed:
            pass
        finally:
            if self._ws.close_code is not None:
                self.fechamento = self._ws.close_code

    async def _ao_iniciar(self, msg: dict[str, Any]) -> None:
        if self.modo == "mudo":
            return
        await self._enviar({"tipo": "ack", "id": msg["id"]})
        if self.modo == "falhar":
            await self._enviar({"tipo": "resultado", "id": msg["id"], "ok": False, "detalhe": "falha simulada"})
            return
        if self.publicar:
            try:
                self._publicador = await _PublicadorLiveKit.iniciar(msg)
            except Exception as erro:  # noqa: BLE001 - o erro vira resposta para o backend
                await self._enviar({"tipo": "resultado", "id": msg["id"], "ok": False, "detalhe": str(erro)[:300]})
                return
        self.sessao_ativa = msg["id"]
        await self._enviar({"tipo": "resultado", "id": msg["id"], "ok": True, "detalhe": ""})

    async def _ao_parar(self, msg: dict[str, Any]) -> None:
        if self.modo == "mudo":
            return
        await self._enviar({"tipo": "ack", "id": msg["id"]})
        await self._parar_publicacao()
        self.sessao_ativa = None
        await self._enviar({"tipo": "resultado", "id": msg["id"], "ok": True, "detalhe": ""})

    async def _parar_publicacao(self) -> None:
        if self._publicador is not None:
            await self._publicador.parar()
            self._publicador = None

    async def _enviar(self, mensagem: dict[str, Any]) -> None:
        assert self._ws is not None
        log.info("-> %s", _resumo(mensagem))
        await self._ws.send(json.dumps(mensagem))


class _PublicadorLiveKit:
    """Publica um vídeo sintético com os parâmetros e o token recebidos no 'iniciar'."""

    def __init__(self, sala, fonte, tarefa) -> None:
        self._sala, self._fonte, self._tarefa = sala, fonte, tarefa

    @classmethod
    async def iniciar(cls, msg: dict[str, Any]) -> _PublicadorLiveKit:
        if rtc is None:
            raise RuntimeError("pacote 'livekit' não instalado (pip install livekit)")
        video = msg["video"]
        sala = rtc.Room()
        await sala.connect(msg["livekit_url"], msg["token"], rtc.RoomOptions(auto_subscribe=False))
        fonte = rtc.VideoSource(video["largura"], video["altura"], is_screencast=True)
        track = rtc.LocalVideoTrack.create_video_track("tela", fonte)
        opcoes = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_SCREENSHARE,
            video_codec=rtc.VideoCodec.H264 if video["codec"] == "h264" else rtc.VideoCodec.VP8,
            video_encoder="ENCODER_BACKEND_SOFTWARE",
            simulcast=False,
            video_encoding=rtc.VideoEncoding(max_framerate=video["fps"], max_bitrate=video["bitrate_kbps"] * 1000),
        )
        await sala.local_participant.publish_track(track, opcoes)
        tarefa = asyncio.create_task(cls._gerar_frames(fonte, video["largura"], video["altura"], video["fps"]))
        return cls(sala, fonte, tarefa)

    @staticmethod
    async def _gerar_frames(fonte, largura: int, altura: int, fps: int) -> None:
        n = 0
        while True:
            cor = bytes(((n * 4) % 256, 80, 160, 255))
            fonte.capture_frame(rtc.VideoFrame(largura, altura, rtc.VideoBufferType.RGBA, cor * (largura * altura)))
            n += 1
            await asyncio.sleep(1 / fps)

    async def parar(self) -> None:
        self._tarefa.cancel()
        await asyncio.gather(self._tarefa, return_exceptions=True)
        await self._sala.disconnect()


def _resumo(mensagem: dict[str, Any]) -> str:
    oculto = {k: ("***" if k in ("token", "device_token") else v) for k, v in mensagem.items()}
    return json.dumps(oculto, ensure_ascii=False)


async def _principal(args: argparse.Namespace) -> None:
    cliente = ClienteFalso(args.url, modo=args.modo, publicar=args.publicar)
    if args.parear:
        resposta = await cliente.conectar(codigo=args.parear, nome=args.nome)
    else:
        if not ARQUIVO_TOKEN.exists():
            raise SystemExit("Sem token salvo. Rode com --parear CODIGO (gere o código com /parear no Discord).")
        resposta = await cliente.conectar(token=ARQUIVO_TOKEN.read_text().strip())

    if resposta["tipo"] == "erro":
        raise SystemExit(f"Recusado pelo backend: {resposta['mensagem']}")
    if resposta["tipo"] == "pareado":
        ARQUIVO_TOKEN.write_text(resposta["device_token"])
        print(f"Pareado como '{resposta['dispositivo']}' de {resposta['usuario']}. Token salvo em {ARQUIVO_TOKEN.name}.")
    else:
        print(f"Conectado como '{resposta['dispositivo']}' de {resposta['usuario']}.")
    print(f"Modo: {args.modo}{' + publicando no LiveKit' if args.publicar else ''}. Ctrl+C para sair.")
    try:
        fechamento = await cliente.esperar_fechamento(timeout=None)
        print(f"O servidor fechou a conexão (código {fechamento}).")
    finally:
        await cliente.fechar()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/cliente")
    parser.add_argument("--parear", metavar="CODIGO", help="código gerado pelo /parear")
    parser.add_argument("--nome", default="PC falso", help="nome do dispositivo ao parear")
    parser.add_argument("--modo", choices=["normal", "falhar", "mudo"], default="normal")
    parser.add_argument("--publicar", action="store_true", help="publica vídeo de teste no LiveKit (requer livekit)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(_principal(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
