"use strict";

// Página do espectador. O token vem de POST /api/assistir/<chave>; o HTML não carrega nenhum.
(() => {
  const $ = (id) => document.getElementById(id);
  const el = {
    palco: $("palco"), aviso: $("aviso"), girando: $("girando"), avisoTitulo: $("aviso-titulo"),
    avisoTexto: $("aviso-texto"), tentar: $("tentar"), selo: $("selo"), sala: $("sala"),
    transmissor: $("transmissor"), espectadores: $("espectadores"), contagem: $("contagem"),
    telaCheia: $("tela-cheia"),
  };
  const chave = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const tiles = new Map(); // trackSid -> { tile, track }
  let sala = null;

  class LinkInvalido extends Error {}

  function avisar(titulo, texto = "", { carregando = false, tentar = false } = {}) {
    el.avisoTitulo.textContent = titulo;
    el.avisoTexto.textContent = texto;
    el.girando.hidden = !carregando;
    el.tentar.hidden = !tentar;
    el.aviso.hidden = false;
  }

  async function pedirAcesso() {
    const resposta = await fetch(`/api/assistir/${encodeURIComponent(chave)}`, { method: "POST", cache: "no-store" });
    if (resposta.status === 404) throw new LinkInvalido();
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
    return resposta.json();
  }

  const remotos = () => (sala ? [...sala.remoteParticipants.values()] : []);
  const transmissores = () => remotos().filter((p) => p.identity.startsWith("pc-"));

  function atualizarContagem() {
    const total = remotos().filter((p) => p.identity.startsWith("espectador-")).length + 1;
    el.contagem.textContent = String(total);
    el.espectadores.title = total === 1 ? "Só você assistindo" : `${total} pessoas assistindo`;
    el.espectadores.hidden = !sala;
  }

  function atualizarEstado() {
    el.selo.hidden = tiles.size === 0;
    el.palco.dataset.quantidade = String(tiles.size);
    if (tiles.size > 0) {
      el.aviso.hidden = true;
    } else if (transmissores().length > 0) {
      avisar("Aguardando o vídeo…", "", { carregando: true });
    } else {
      avisar("A transmissão terminou", "Se alguém começar a transmitir nesta sala, aparece aqui sozinho.");
    }
  }

  function adicionar(track, publicacao, participante) {
    if (track.kind !== "video") return;
    const tile = document.createElement("figure");
    tile.className = "tile";
    const video = track.attach();
    video.muted = true;
    video.playsInline = true;
    const nome = document.createElement("figcaption");
    nome.textContent = participante.name || participante.identity;
    const resolucao = document.createElement("figcaption");
    resolucao.className = "resolucao";
    const medir = () => {
      if (video.videoWidth) resolucao.textContent = `${video.videoWidth}×${video.videoHeight}`;
    };
    video.addEventListener("loadedmetadata", medir);
    video.addEventListener("resize", medir);
    tile.addEventListener("dblclick", () => alternarTelaCheia(tile));
    tile.append(video, nome, resolucao);
    el.palco.append(tile);
    tiles.set(publicacao.trackSid, { tile, track });
    atualizarEstado();
  }

  function remover(track, publicacao) {
    const item = tiles.get(publicacao.trackSid);
    if (!item) return;
    track.detach();
    item.tile.remove();
    tiles.delete(publicacao.trackSid);
    atualizarEstado();
  }

  function limpar() {
    for (const { tile, track } of tiles.values()) {
      track.detach();
      tile.remove();
    }
    tiles.clear();
    sala = null;
    el.selo.hidden = true;
    el.espectadores.hidden = true;
  }

  function alternarTelaCheia(alvo = el.palco) {
    if (document.fullscreenElement) document.exitFullscreen();
    else alvo.requestFullscreen?.().catch(() => {});
  }

  async function entrar() {
    avisar("Conectando…", "", { carregando: true });
    if (!window.LivekitClient) {
      avisar("Não foi possível carregar o player", "Verifique sua conexão e tente de novo.", { tentar: true });
      return;
    }
    const { Room, RoomEvent } = window.LivekitClient;

    let acesso;
    try {
      acesso = await pedirAcesso();
    } catch (erro) {
      if (erro instanceof LinkInvalido) {
        avisar("Link expirado", "Essa transmissão terminou ou o link está incompleto. Peça o link novo no Discord.");
      } else {
        avisar("Sem conexão com o servidor", "Tente de novo em alguns segundos.", { tentar: true });
      }
      return;
    }
    el.sala.textContent = `Sala ${acesso.sala}`;
    el.transmissor.textContent = `${acesso.transmissor} está transmitindo`;
    document.title = `${acesso.transmissor} ao vivo · ${acesso.sala}`;

    const atual = new Room({ adaptiveStream: true });
    sala = atual;
    const mudouParticipante = () => { atualizarContagem(); atualizarEstado(); };
    atual
      .on(RoomEvent.TrackSubscribed, adicionar)
      .on(RoomEvent.TrackUnsubscribed, remover)
      .on(RoomEvent.ParticipantConnected, mudouParticipante)
      .on(RoomEvent.ParticipantDisconnected, mudouParticipante)
      .on(RoomEvent.Reconnecting, () => avisar("Reconectando…", "", { carregando: true }))
      .on(RoomEvent.Reconnected, atualizarEstado)
      .on(RoomEvent.Disconnected, () => {
        if (sala !== atual) return;
        limpar();
        avisar("Desconectado", "A conexão com o vídeo caiu.", { tentar: true });
      });
    try {
      await atual.connect(acesso.livekit_url, acesso.token);
    } catch (erro) {
      limpar();
      avisar("Não foi possível conectar ao vídeo", String(erro?.message || erro), { tentar: true });
      return;
    }
    atualizarContagem();
    atualizarEstado();
  }

  el.tentar.addEventListener("click", entrar);
  el.telaCheia.addEventListener("click", () => alternarTelaCheia());
  document.addEventListener("keydown", (evento) => {
    if (evento.key.toLowerCase() === "f" && !evento.ctrlKey && !evento.metaKey && !evento.altKey) alternarTelaCheia();
  });
  window.addEventListener("pagehide", () => sala?.disconnect());
  entrar();
})();
