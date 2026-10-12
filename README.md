# Live dos Dude

Transmissão de tela de baixa latência disparada por slash command no Discord.

## ⬇️ Baixar o app (Windows 10/11)

[![Baixar o instalador](https://img.shields.io/github/v/release/SHAMartins/live-dos-dude?label=Baixar%20o%20instalador&style=for-the-badge&logo=windows)](https://github.com/SHAMartins/live-dos-dude/releases/latest)

1. Na [última versão](https://github.com/SHAMartins/live-dos-dude/releases/latest), baixe
   **`LiveDosDude-<versão>-instalador.exe`** e abra.
2. Se o Windows avisar "O Windows protegeu o computador": *Mais informações → Executar assim mesmo*
   (o app não tem assinatura digital paga).
3. No Discord, use `/parear` e digite o código na janela do app. Pronto: daí em diante é só `/transmitir`.

Só quem **transmite** precisa do app. Quem assiste só clica no link que o bot posta.

---

`/transmitir sala:dev` → o bot avisa o PC por WebSocket → o cliente desktop captura a
tela e o som (menos o Discord), codifica na CPU e publica no LiveKit → o bot posta um link e os
espectadores assistem pelo navegador.

| Bloco | Pasta | Status |
|---|---|---|
| SFU (LiveKit + Caddy na OCI) | [infra_sfu/](infra_sfu/README.md) | pronto; no ar pelo LiveKit Cloud enquanto falta capacidade Ampere na OCI |
| Backend (bot Discord + API Quart) | [backend_bot/](backend_bot/README.md) | no ar na OCI, 69 testes |
| Cliente desktop (bandeja + captura) | [cliente_desktop/](cliente_desktop/README.md) | instalador publicado, 45 testes |
| Página do espectador | [backend_bot/app/web/](backend_bot/README.md#página-do-espectador) | no ar, com volume |

- O que falta fazer: [PENDENCIAS.md](PENDENCIAS.md)
- Decisões de arquitetura e motivos: [docs/arquitetura.md](docs/arquitetura.md)
- Protocolo backend ↔ cliente desktop: [docs/protocolo.md](docs/protocolo.md)
