"""Janela de status/pareamento/configuração (customtkinter). Roda na thread principal.

Não chama o controlador direto: lê o PainelEstado a cada 250 ms e dispara as AcoesUI,
que agendam o trabalho na thread asyncio.
"""

from __future__ import annotations

import os
import queue
import time
from collections.abc import Callable
from dataclasses import dataclass

import customtkinter as ctk

from cliente.config import ConfigCliente
from cliente.estado import Estado, PainelEstado, Snapshot
from cliente.ui.aparencia import COR, TITULO
from cliente import sistema
from cliente.ui.bandeja import Bandeja
from cliente.ui.fechamento import ROTULOS, DialogoFechar, escolha_valida
from cliente.ui.transmissao import PainelTransmissao

INTERVALO_MS = 250


@dataclass(frozen=True)
class AcoesUI:
    parear: Callable[[str], None]
    parar: Callable[[], None]
    reconectar: Callable[[], None]
    salvar: Callable[[str, str], None]  # (backend_url, nome_dispositivo)
    salvar_config: Callable[[], None]   # grava fonte/qualidade no config.json
    aplicar_agora: Callable[[], None]   # troca fonte/qualidade da transmissão no ar
    sair: Callable[[], None]


class Janela(ctk.CTk):
    def __init__(self, painel: PainelEstado, acoes: AcoesUI, config: ConfigCliente,
                 pedidos: queue.Queue[str], estatisticas: Callable[[], tuple[float, float, str]],
                 bandeja: Bandeja | None = None, *, iniciar_oculta: bool = False) -> None:
        ctk.set_appearance_mode("system")
        super().__init__()
        self._painel, self._acoes, self._pedidos = painel, acoes, pedidos
        self._config = config
        self._transmitindo = False
        self._estatisticas, self._bandeja = estatisticas, bandeja
        self._cpu_antes = (time.perf_counter(), time.process_time())
        self._cpu_pct = 0.0
        self._versao = -1
        self._estado_anterior: Estado | None = None

        self.title("Live dos Dude")
        self.geometry("420x700")
        self.minsize(380, 520)
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar_janela)
        self.grid_columnconfigure(0, weight=1)
        self._montar(config)
        if iniciar_oculta:
            self.withdraw()
        icone = sistema.recurso("icone.ico")
        if icone.exists():  # o customtkinter troca o ícone por volta de 200 ms depois de abrir
            self.after(300, lambda: self.iconbitmap(str(icone)))
        self.after(0, self._tique)

    # --- fechar a janela ---------------------------------------------------------

    def _ao_fechar_janela(self) -> None:
        escolha = escolha_valida(self._config.ao_fechar)
        if escolha == "bandeja":
            self.withdraw()
        elif escolha == "sair":
            self._acoes.sair()
        else:
            DialogoFechar(self, self._transmitindo, self._decidiu_fechar)

    def _decidiu_fechar(self, escolha: str, lembrar: bool) -> None:
        if lembrar:
            self._config.ao_fechar = escolha  # type: ignore[assignment]
            self._acoes.salvar_config()
            self._opcao_fechar.set(ROTULOS[escolha])
        if escolha == "bandeja":
            self.withdraw()
        else:
            self._acoes.sair()  # encerra a transmissão (se houver) e fecha o app

    def _mudou_ao_fechar(self, rotulo: str) -> None:
        self._config.ao_fechar = next(k for k, v in ROTULOS.items() if v == rotulo)  # type: ignore[assignment]
        self._acoes.salvar_config()

    # --- layout ---------------------------------------------------------------

    def _montar(self, config: ConfigCliente) -> None:
        negrito = ctk.CTkFont(size=13, weight="bold")

        topo = ctk.CTkFrame(self, fg_color="transparent")
        topo.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 4))
        topo.grid_columnconfigure(1, weight=1)
        self._bolinha = ctk.CTkLabel(topo, text="●", font=ctk.CTkFont(size=22), width=24)
        self._bolinha.grid(row=0, column=0)
        self._titulo = ctk.CTkLabel(topo, text="", font=ctk.CTkFont(size=18, weight="bold"), anchor="w")
        self._titulo.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self._identidade = ctk.CTkLabel(self, text="", anchor="w", text_color=("gray35", "gray65"))
        self._identidade.grid(row=1, column=0, sticky="ew", padx=16)
        self._mensagem = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=360)
        self._mensagem.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 8))

        # Transmissão em andamento
        self._quadro_ao_vivo = ctk.CTkFrame(self)
        self._quadro_ao_vivo.grid(row=3, column=0, sticky="ew", padx=16, pady=4)
        self._quadro_ao_vivo.grid_columnconfigure(0, weight=1)
        self._sala = ctk.CTkLabel(self._quadro_ao_vivo, text="", font=negrito, anchor="w")
        self._sala.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        self._detalhes = ctk.CTkLabel(self._quadro_ao_vivo, text="", anchor="w", justify="left")
        self._detalhes.grid(row=1, column=0, sticky="ew", padx=12)
        self._medidas = ctk.CTkLabel(self._quadro_ao_vivo, text="", anchor="w", text_color=("gray35", "gray65"))
        self._medidas.grid(row=2, column=0, sticky="ew", padx=12)
        self._aviso = ctk.CTkLabel(self._quadro_ao_vivo, text="", anchor="w", justify="left", wraplength=340,
                                   text_color=("#a15c00", "#f0a33a"))
        self._aviso.grid(row=3, column=0, sticky="ew", padx=12)
        self._aviso.grid_remove()
        ctk.CTkButton(self._quadro_ao_vivo, text="Parar transmissão", fg_color=COR[Estado.TRANSMITINDO],
                      hover_color="#c13a3f", command=self._acoes.parar).grid(row=4, column=0, sticky="ew", padx=12, pady=10)

        # Pareamento
        self._quadro_parear = ctk.CTkFrame(self)
        self._quadro_parear.grid(row=4, column=0, sticky="ew", padx=16, pady=4)
        self._quadro_parear.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self._quadro_parear, text="Código do /parear", font=negrito, anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))
        self._codigo = ctk.CTkEntry(self._quadro_parear, placeholder_text="ABC-234")
        self._codigo.grid(row=1, column=0, sticky="ew", padx=(12, 6), pady=(0, 12))
        self._codigo.bind("<Return>", lambda _: self._parear())
        ctk.CTkButton(self._quadro_parear, text="Parear", width=90, command=self._parear).grid(
            row=1, column=1, padx=(0, 12), pady=(0, 12))

        self._botao_reconectar = ctk.CTkButton(self, text="Reconectar agora", command=self._acoes.reconectar)
        self._botao_reconectar.grid(row=5, column=0, sticky="ew", padx=16, pady=4)

        # Fonte e qualidade das transmissões
        self._painel_transmissao = PainelTransmissao(self, config, self._acoes.salvar_config, self._acoes.aplicar_agora)
        self._painel_transmissao.grid(row=6, column=0, sticky="ew", padx=16, pady=(12, 4))

        # Conexão (raramente muda: fica recolhida)
        self._botao_conexao = ctk.CTkButton(self, text="Conexão ▸", anchor="w", fg_color="transparent",
                                            text_color=("gray20", "gray80"), hover_color=("gray85", "gray20"),
                                            command=self._alternar_conexao)
        self._botao_conexao.grid(row=7, column=0, sticky="ew", padx=16, pady=(8, 0))
        ajustes = ctk.CTkFrame(self)
        ajustes.grid(row=8, column=0, sticky="ew", padx=16, pady=(4, 4))
        ajustes.grid_columnconfigure(0, weight=1)
        self._ajustes = ajustes
        ctk.CTkLabel(ajustes, text="Endereço do backend", anchor="w").grid(row=1, column=0, sticky="ew", padx=12)
        self._url = ctk.CTkEntry(ajustes)
        self._url.insert(0, config.backend_url)
        self._url.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
        ctk.CTkLabel(ajustes, text="Nome deste PC", anchor="w").grid(row=3, column=0, sticky="ew", padx=12)
        self._nome = ctk.CTkEntry(ajustes)
        self._nome.insert(0, config.nome_dispositivo)
        self._nome.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 6))
        ctk.CTkButton(ajustes, text="Salvar e reconectar", command=self._salvar).grid(
            row=5, column=0, sticky="ew", padx=12, pady=(4, 6))
        self._iniciar_com_windows = ctk.CTkCheckBox(ajustes, text="Iniciar com o Windows (abre na bandeja)",
                                                    command=self._alternar_inicio)
        if sistema.iniciar_com_windows_ativo():
            self._iniciar_com_windows.select()
        if not sistema.empacotado():
            self._iniciar_com_windows.configure(state="disabled", text="Iniciar com o Windows (só no executável)")
        self._iniciar_com_windows.grid(row=6, column=0, sticky="w", padx=12, pady=(0, 6))
        ctk.CTkLabel(ajustes, text="Ao fechar a janela", anchor="w").grid(row=7, column=0, sticky="ew", padx=12)
        self._opcao_fechar = ctk.CTkOptionMenu(ajustes, values=list(ROTULOS.values()), command=self._mudou_ao_fechar)
        self._opcao_fechar.set(ROTULOS[escolha_valida(config.ao_fechar)])
        self._opcao_fechar.grid(row=8, column=0, sticky="ew", padx=12, pady=(0, 12))

        ajustes.grid_remove()

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=9, column=0, sticky="ew", padx=16, pady=(8, 12))
        rodape.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(rodape, text="Para encerrar de vez: Sair, ou ícone da bandeja → Sair.", anchor="w",
                     text_color=("gray40", "gray60"), font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(rodape, text="Sair", width=70, fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"), command=self._acoes.sair).grid(row=0, column=1)

    # --- ações ----------------------------------------------------------------

    def _parear(self) -> None:
        codigo = self._codigo.get().strip()
        if codigo:
            self._acoes.parear(codigo)
            self._codigo.delete(0, "end")

    def _salvar(self) -> None:
        url, nome = self._url.get().strip(), self._nome.get().strip()
        if url and nome:
            self._acoes.salvar(url, nome)

    def _alternar_inicio(self) -> None:
        try:
            sistema.definir_iniciar_com_windows(bool(self._iniciar_com_windows.get()))
        except (OSError, RuntimeError):
            self._iniciar_com_windows.toggle()  # não conseguiu gravar: volta a caixa ao estado real

    def _alternar_conexao(self) -> None:
        aberta = self._ajustes.winfo_ismapped()
        self._ajustes.grid_remove() if aberta else self._ajustes.grid()
        self._botao_conexao.configure(text="Conexão ▸" if aberta else "Conexão ▾")

    def mostrar(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    # --- atualização periódica --------------------------------------------------

    def _tique(self) -> None:
        while True:
            try:
                pedido = self._pedidos.get_nowait()
            except queue.Empty:
                break
            if pedido == "abrir":
                self.mostrar()
            elif pedido == "sair":
                self._acoes.sair()
                return

        versao, snapshot = self._painel.ler()
        if versao != self._versao:
            self._versao = versao
            self._renderizar(snapshot)
            if self._bandeja is not None:
                self._bandeja.atualizar(snapshot)
        if snapshot.estado == Estado.TRANSMITINDO:
            fps, ms, aviso = self._estatisticas()
            self._medidas.configure(
                text=f"Capturando a {fps:.0f} fps · {ms:.1f} ms por quadro · CPU do app: {self._medir_cpu():.0f}%")
            self._aviso.configure(text=aviso)
            self._aviso.grid() if aviso else self._aviso.grid_remove()
        self.after(INTERVALO_MS, self._tique)

    def _medir_cpu(self) -> float:
        """Uso de CPU do app (todas as threads, inclusive o encoder nativo) como % da CPU inteira.

        Janela de ~1,5 s para o número não pular a cada tique.
        """
        agora, cpu = time.perf_counter(), time.process_time()
        antes_t, antes_c = self._cpu_antes
        if agora - antes_t >= 1.5:
            self._cpu_pct = (cpu - antes_c) / (agora - antes_t) / (os.cpu_count() or 1) * 100
            self._cpu_antes = (agora, cpu)
        return self._cpu_pct

    def _renderizar(self, s: Snapshot) -> None:
        self._bolinha.configure(text_color=COR[s.estado])
        self._titulo.configure(text=TITULO[s.estado])
        self._identidade.configure(text=f"{s.usuario} · {s.dispositivo}" if s.usuario else "")
        self._identidade.grid() if s.usuario else self._identidade.grid_remove()
        self._mensagem.configure(text=s.mensagem)
        self._mensagem.grid() if s.mensagem else self._mensagem.grid_remove()

        if s.estado == Estado.TRANSMITINDO and s.resolucao:
            largura, altura = s.resolucao
            self._sala.configure(text=f"Sala: {s.sala}")
            self._detalhes.configure(
                text=f"{s.fonte}\n{largura}x{altura} · {s.fps_alvo} fps · {s.bitrate_kbps} kbps · {s.codec.upper()}"
                     f" · {'com som' if s.audio else 'sem som'}")
            self._quadro_ao_vivo.grid()
        else:
            self._quadro_ao_vivo.grid_remove()

        self._transmitindo = s.estado == Estado.TRANSMITINDO
        self._painel_transmissao.definir_transmitindo(self._transmitindo)
        self._quadro_parear.grid() if s.estado == Estado.PRECISA_PAREAR else self._quadro_parear.grid_remove()
        # Sem conexão definida (ex.: endereço errado), abre a seção de conexão para corrigir.
        if s.estado in (Estado.CONECTANDO, Estado.BLOQUEADO) and not self._ajustes.winfo_ismapped():
            if self._estado_anterior not in (Estado.CONECTANDO, Estado.BLOQUEADO):
                self._alternar_conexao()
        mostrar_reconectar = s.estado in (Estado.CONECTANDO, Estado.BLOQUEADO)
        self._botao_reconectar.grid() if mostrar_reconectar else self._botao_reconectar.grid_remove()

        # Sem token, a janela aparece sozinha para o usuário poder parear.
        if s.estado == Estado.PRECISA_PAREAR and self._estado_anterior != Estado.PRECISA_PAREAR:
            self.mostrar()
        self._estado_anterior = s.estado
