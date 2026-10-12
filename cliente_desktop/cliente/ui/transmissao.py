"""Painel "Transmissão" (fonte + qualidade) e o seletor de fonte estilo "Go Live" do Discord.

Tudo aqui roda na thread do Tk. Listar janelas e gerar miniaturas pode levar alguns
segundos, então isso vai para uma thread separada e volta por fila, sem travar a janela.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

import customtkinter as ctk
from PIL import Image

from cliente.config import BITRATE_MAX, BITRATE_MIN, OPCOES_FPS, ConfigCliente, bitrate_sugerido
from cliente.media import fontes

RESOLUCOES = {"480p": "480p", "720p": "720p", "1080p": "1080p", "Nativa (sem reduzir)": "nativa"}
LARGURA_CARTAO = 200


class PainelTransmissao(ctk.CTkFrame):
    """Padrões das próximas transmissões. Cada mudança é salva na hora."""

    def __init__(self, mestre, config: ConfigCliente, salvar: Callable[[], None],
                 aplicar_agora: Callable[[], None]) -> None:
        super().__init__(mestre)
        self._config, self._salvar, self._aplicar_agora = config, salvar, aplicar_agora
        self._transmitindo = False
        self._pendente = False  # mudou algo com a transmissão no ar
        self.grid_columnconfigure(1, weight=1)
        negrito = ctk.CTkFont(size=13, weight="bold")
        suave = ("gray35", "gray65")

        ctk.CTkLabel(self, text="Transmissão", font=negrito, anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="ew", padx=12, pady=(10, 6))

        ctk.CTkLabel(self, text="Fonte", anchor="w").grid(row=1, column=0, sticky="w", padx=(12, 8))
        self._fonte = ctk.CTkLabel(self, text="", anchor="w")
        self._fonte.grid(row=1, column=1, sticky="ew")
        ctk.CTkButton(self, text="Escolher…", width=90, command=self._abrir_seletor).grid(
            row=1, column=2, padx=12, pady=2)

        ctk.CTkLabel(self, text="Resolução", anchor="w").grid(row=2, column=0, sticky="w", padx=(12, 8), pady=4)
        rotulo_resolucao = next(r for r, v in RESOLUCOES.items() if v == config.resolucao)
        self._resolucao = ctk.CTkOptionMenu(self, values=list(RESOLUCOES), command=self._mudou_resolucao)
        self._resolucao.set(rotulo_resolucao)
        self._resolucao.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=4)

        ctk.CTkLabel(self, text="FPS", anchor="w").grid(row=3, column=0, sticky="w", padx=(12, 8), pady=4)
        self._fps = ctk.CTkSegmentedButton(self, values=[str(f) for f in OPCOES_FPS], command=self._mudou_fps)
        self._fps.set(str(config.fps))
        self._fps.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=4)

        ctk.CTkLabel(self, text="Bitrate", anchor="w").grid(row=4, column=0, sticky="w", padx=(12, 8), pady=4)
        self._bitrate = ctk.CTkSlider(self, from_=BITRATE_MIN, to=BITRATE_MAX,
                                      number_of_steps=(BITRATE_MAX - BITRATE_MIN) // 100, command=self._mudou_bitrate)
        self._bitrate.set(config.bitrate_kbps)
        self._bitrate.grid(row=4, column=1, sticky="ew", pady=4)
        self._rotulo_bitrate = ctk.CTkLabel(self, text="", width=90, anchor="e")
        self._rotulo_bitrate.grid(row=4, column=2, padx=12)

        self._sugestao = ctk.CTkButton(self, text="", height=24, fg_color="transparent", border_width=1,
                                       text_color=suave, command=self._usar_sugestao)
        self._sugestao.grid(row=5, column=1, columnspan=2, sticky="w", pady=(0, 4))

        self._audio = ctk.BooleanVar(value=config.audio)
        ctk.CTkCheckBox(self, text="Transmitir o som do PC", variable=self._audio, command=self._mudou_audio).grid(
            row=6, column=0, columnspan=3, sticky="w", padx=12, pady=(6, 0))
        ctk.CTkLabel(self, anchor="w", justify="left", wraplength=340, text_color=suave, font=ctk.CTkFont(size=11),
                     text="Todo o som do PC, menos o Discord: as vozes da call não vão para quem assiste.").grid(
            row=7, column=0, columnspan=3, sticky="ew", padx=(42, 12))

        self._dica = ctk.CTkLabel(self, anchor="w", justify="left", wraplength=340, text_color=suave,
                                  font=ctk.CTkFont(size=11),
                                  text="Vale para as próximas transmissões. O que for digitado no /transmitir "
                                       "tem prioridade naquela vez.")
        self._dica.grid(row=8, column=0, columnspan=3, sticky="ew", padx=12, pady=(6, 6))

        self._aplicar = ctk.CTkButton(self, text="Aplicar agora (o vídeo para ~1 s)", command=self._clicou_aplicar)
        self._aplicar.grid(row=9, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 10))
        self._aplicar.grid_remove()
        self._atualizar_textos()

    # --- chamado pela janela a cada mudança de estado ---------------------------

    def definir_transmitindo(self, transmitindo: bool) -> None:
        if transmitindo != self._transmitindo:
            self._transmitindo = transmitindo
            self._pendente = False
            self._atualizar_aplicar()

    # --- mudanças ---------------------------------------------------------------

    def _mudou_resolucao(self, rotulo: str) -> None:
        self._config.resolucao = RESOLUCOES[rotulo]
        self._registrar_mudanca()

    def _mudou_fps(self, valor: str) -> None:
        self._config.fps = int(valor)
        self._registrar_mudanca()

    def _mudou_bitrate(self, valor: float) -> None:
        self._config.bitrate_kbps = int(round(valor / 100) * 100)
        self._registrar_mudanca()

    def _mudou_audio(self) -> None:
        self._config.audio = bool(self._audio.get())
        self._registrar_mudanca()

    def _usar_sugestao(self) -> None:
        self._config.bitrate_kbps = bitrate_sugerido(self._config.resolucao, self._config.fps)
        self._bitrate.set(self._config.bitrate_kbps)
        self._registrar_mudanca()

    def _escolheu_fonte(self, fonte: dict) -> None:
        self._config.fonte = fonte
        self._registrar_mudanca()

    def _registrar_mudanca(self) -> None:
        self._salvar()
        if self._transmitindo:
            self._pendente = True
        self._atualizar_textos()
        self._atualizar_aplicar()

    def _clicou_aplicar(self) -> None:
        self._pendente = False
        self._atualizar_aplicar()
        self._aplicar_agora()

    def _abrir_seletor(self) -> None:
        SeletorFonte(self.winfo_toplevel(), self._config.fonte, self._escolheu_fonte)

    # --- textos -----------------------------------------------------------------

    def _atualizar_textos(self) -> None:
        self._fonte.configure(text=_encurtar(fontes.descrever(self._config.fonte), 34))
        self._rotulo_bitrate.configure(text=f"{self._config.bitrate_kbps} kbps")
        sugerido = bitrate_sugerido(self._config.resolucao, self._config.fps)
        self._sugestao.configure(text=f"Usar o sugerido: {sugerido} kbps")
        self._sugestao.grid() if sugerido != self._config.bitrate_kbps else self._sugestao.grid_remove()

    def _atualizar_aplicar(self) -> None:
        self._aplicar.grid() if self._transmitindo and self._pendente else self._aplicar.grid_remove()


class SeletorFonte(ctk.CTkToplevel):
    """Janela modal com abas "Telas" e "Aplicativos", com miniaturas."""

    def __init__(self, mestre, atual: dict | None, escolher: Callable[[dict], None]) -> None:
        super().__init__(mestre)
        self._escolher = escolher
        self._atual = atual
        self._resultados: queue.Queue = queue.Queue()
        self._imagens: list[ctk.CTkImage] = []  # referências vivas (senão o Tk descarta as imagens)

        self.title("O que transmitir")
        self.geometry("700x520")
        self.minsize(480, 360)
        self.transient(mestre)
        self.after(100, self.grab_set)  # modal (com atraso: o customtkinter ainda está montando a janela)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._abas = ctk.CTkTabview(self)
        self._abas.grid(row=0, column=0, sticky="nsew", padx=12, pady=(8, 4))
        self._listas: dict[str, ctk.CTkScrollableFrame] = {}
        for nome in ("Telas", "Aplicativos"):
            aba = self._abas.add(nome)
            aba.grid_columnconfigure(0, weight=1)
            aba.grid_rowconfigure(0, weight=1)
            lista = ctk.CTkScrollableFrame(aba)
            lista.grid(row=0, column=0, sticky="nsew")
            self._listas[nome] = lista
        if atual and atual.get("tipo") == "janela":
            self._abas.set("Aplicativos")

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        rodape.grid_columnconfigure(0, weight=1)
        self._status = ctk.CTkLabel(rodape, text="Procurando telas e janelas…", anchor="w",
                                    text_color=("gray35", "gray65"))
        self._status.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(rodape, text="Atualizar", width=90, command=self._carregar).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(rodape, text="Cancelar", width=90, fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"), command=self.destroy).grid(row=0, column=2)
        self._carregar()

    def _carregar(self) -> None:
        for lista in self._listas.values():
            for filho in lista.winfo_children():
                filho.destroy()
        self._imagens.clear()
        self._status.configure(text="Procurando telas e janelas…")
        threading.Thread(target=self._buscar, name="seletor", daemon=True).start()
        self.after(100, self._receber)

    def _buscar(self) -> None:
        """Fora da thread do Tk: só calcula e enfileira."""
        try:
            telas = fontes.listar_telas()
            janelas = fontes.listar_janelas()
            self._resultados.put(("contagem", len(telas), len(janelas)))
            for tela in telas:
                titulo = f"Tela {tela.indice + 1} · {tela.largura}x{tela.altura}" + (" (principal)" if tela.principal else "")
                self._resultados.put(("Telas", tela.indice, titulo, fontes.miniatura_tela(tela), tela.como_fonte()))
            for i, janela in enumerate(janelas):
                titulo = f"{_encurtar(janela.titulo, 30)}\n{janela.processo}"
                self._resultados.put(("Aplicativos", i, titulo, fontes.miniatura_janela(janela), janela.como_fonte()))
            self._resultados.put(("fim",))
        except Exception as erro:  # noqa: BLE001 - mostra o erro no seletor em vez de sumir com ele
            self._resultados.put(("erro", str(erro)))

    def _receber(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                item = self._resultados.get_nowait()
                if item[0] == "contagem":
                    self._status.configure(text=f"{item[1]} tela(s) e {item[2]} janela(s). Gerando miniaturas…")
                elif item[0] == "fim":
                    self._status.configure(text="Clique no que você quer transmitir.")
                    return
                elif item[0] == "erro":
                    self._status.configure(text=f"Erro ao listar: {item[1]}")
                    return
                else:
                    self._adicionar_cartao(*item)
        except queue.Empty:
            pass
        self.after(100, self._receber)

    def _adicionar_cartao(self, aba: str, posicao: int, titulo: str, miniatura, fonte: dict) -> None:
        lista = self._listas[aba]
        imagem = None
        if miniatura is not None:
            pil = Image.fromarray(miniatura)
            altura = round(pil.height * LARGURA_CARTAO / pil.width)
            imagem = ctk.CTkImage(light_image=pil, dark_image=pil, size=(LARGURA_CARTAO, min(altura, 140)))
            self._imagens.append(imagem)
        escolhida = _mesma_fonte(fonte, self._atual)
        cartao = ctk.CTkButton(
            lista, text=titulo if imagem else f"{titulo}\n(sem prévia)", image=imagem, compound="top",
            width=LARGURA_CARTAO + 16, height=180, fg_color=("gray75", "gray25") if escolhida else ("gray88", "gray17"),
            hover_color=("gray80", "gray22"), text_color=("gray10", "gray90"), border_width=2 if escolhida else 0,
            command=lambda f=fonte: self._clicou(f),
        )
        cartao.grid(row=posicao // 3, column=posicao % 3, padx=6, pady=6, sticky="n")

    def _clicou(self, fonte: dict) -> None:
        self._escolher(fonte)
        self.destroy()


def _mesma_fonte(a: dict | None, b: dict | None) -> bool:
    if not a or not b or a.get("tipo") != b.get("tipo"):
        return a is None and b is None
    if a["tipo"] == "tela":
        return (a.get("esquerda"), a.get("topo")) == (b.get("esquerda"), b.get("topo"))
    return a.get("processo") == b.get("processo") and a.get("titulo") == b.get("titulo")


def _encurtar(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"
