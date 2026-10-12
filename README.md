# Live dos Dude

Transmissão de tela de baixa latência disparada por slash command no Discord.

`/transmitir sala:dev` → o bot avisa o PC por WebSocket → o cliente desktop captura a
tela, codifica na CPU e publica no LiveKit (OCI) → o bot posta um link e os espectadores assistem pelo navegador.

| Bloco | Pasta | Status |
|---|---|---|
| SFU (LiveKit + Caddy na OCI) | [infra_sfu/](infra_sfu/README.md) | pronto, validado localmente |
| Backend (bot Discord + API Quart) | [backend_bot/](backend_bot/README.md) | pronto, 59 testes, deploy pronto; falta criar o bot e a VM |
| Cliente desktop (bandeja + captura) | [cliente_desktop/](cliente_desktop/README.md) | pronto, 31 testes; validado ponta a ponta com o LiveKit local |
| Página do espectador | [backend_bot/app/web/](backend_bot/README.md#página-do-espectador) | pronta, validada num navegador real |

- O que falta fazer: [PENDENCIAS.md](PENDENCIAS.md)
- Decisões de arquitetura e motivos: [docs/arquitetura.md](docs/arquitetura.md)
- Protocolo backend ↔ cliente desktop: [docs/protocolo.md](docs/protocolo.md)
