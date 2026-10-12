# Decisões de arquitetura

Registro das escolhas feitas na revisão inicial e de por que foram feitas.
Cada bloco deve seguir isto. Mudanças entram aqui antes de entrarem no código.

## Visão geral

```
Discord ──slash cmd──► backend_bot ──WebSocket (push)──► cliente_desktop
                       (bot + API,                         (bandeja, captura,
                        mesmo processo)                     encode por CPU)
                            │                                    │
                            │ assina tokens JWT                  │ WebRTC
                            ▼                                    ▼
                       viewer_web ◄──────── WebRTC ──────── infra_sfu (LiveKit na OCI)
```

## Backend (`backend_bot/`)

| Decisão | Motivo |
|---|---|
| **Quart + Hypercorn no lugar de Flask** | Flask é síncrono (WSGI) e não tem WebSocket nativo. Junto do discord.py, exigiria uma thread separada e `run_coroutine_threadsafe`. O Quart tem a mesma API do Flask, é async e roda no mesmo event loop do bot. |
| **Bot chama o gateway por função, não por HTTP** | Bot e API estão no mesmo processo: o serviço chama `gateway.comando(...)`, que coloca a mensagem na fila de saída do WebSocket do cliente. |
| **`defer()` + followup com `request_id`** | O Discord exige resposta em 3 s, e acordar o cliente e conectar no SFU demora mais. O comando leva um `id`; o cliente responde `ack` (até 5 s) e depois `resultado` (até 20 s); o serviço espera cada um com prazo. |
| **`aiosqlite` em modo WAL** | SQLite síncrono bloquearia o loop do bot. |
| **Pareamento `/parear`** | Vincula `discord_user_id ↔ device_token`. Sem isso qualquer pessoa poderia disparar o PC de outra. O banco guarda só o hash do token. |
| **Token do publicador só libera `screen_share`** | Mesmo um cliente adulterado não abre câmera nem publica em outra sala. Validado contra o LiveKit real. |
| **O cliente é a fonte da verdade ao reconectar** | O `hello` informa a `sessao_ativa`. Cair o WebSocket não derruba o vídeo, então o backend ajusta o banco ao que o PC relata. |
| **Sem resposta depois do `ack` → manda `parar`** | Evita transmissão órfã quando o PC confirmou, mas não terminou de iniciar a tempo. |
| **VM separada da do LiveKit** | Escolha de isolamento: reiniciar, atualizar ou ter problema num lado não afeta o outro, e o LiveKit pode escalar sozinho. Custo: segunda VM, TLS próprio em `api.<domínio>` e webhook do LiveKit pela internet, com assinatura validada. |
| **Webhook do LiveKit com assinatura obrigatória** | O endpoint é público (VMs separadas). Conta espectadores e encerra a sessão quando o PC cai do LiveKit, ignorando reconexões e eventos atrasados. |
| **`/parar` sem resposta → remoção pela API do LiveKit** | O PC pode ter perdido só o WebSocket e continuar transmitindo. |
| **Deploy: imagem própria + Caddy, `network_mode: host`** | Mesmo padrão da VM do LiveKit. Backend só em 127.0.0.1; o Caddy é o único exposto. Desligamento gracioso (1012). Backup pela API de backup do SQLite. |
| **API secret do LiveKit só no backend (env)** | O backend gera tokens JWT curtos por sessão e manda no push. O cliente nunca vê o secret. Nada de segredo no SQLite. |

## Cliente desktop (`cliente_desktop/`)

| Decisão | Motivo |
|---|---|
| **SDK `livekit` (rtc) no lugar de aiortc** | O aiortc não fala o protocolo de sinalização do LiveKit, e o encoder dele roda preso ao GIL. O SDK usa libwebrtc via Rust FFI, com encode em threads nativas. |
| **`video_encoder=ENCODER_BACKEND_SOFTWARE`** | No modo `AUTO` a libwebrtc usaria NVENC/AMF se houvesse placa. Forçar software garante a restrição. Validado: libvpx (VP8) e OpenH264 (H.264), com `power_efficient_encoder=false`. |
| **`simulcast=False`** | Simulcast triplicaria o custo de encode na CPU. |
| **`VideoSource(..., is_screencast=True)`** | Otimiza a libwebrtc para conteúdo de tela (texto nítido). |
| **`degradation_preference` configurável** | `MAINTAIN_RESOLUTION` (padrão) para tela/texto, `MAINTAIN_FRAMERATE` para jogos. |
| **Threads com papéis fixos** | Principal: janela (o Tk exige). `bandeja`: pystray. `asyncio`: WebSocket do backend + sala do LiveKit. `midia`: captura → redução → cursor → encoder. A thread de mídia entrega direto ao `VideoSource` no ritmo do fps, sem fila: se atrasar, descarta o atraso em vez de acumular, e a libwebrtc descarta quadros se o encoder não der conta. |
| **Captura DXGI (dxcam), GDI de reserva** | DXGI: ~3 ms/quadro em 1080p, contra ~20 ms do GDI. A Graphics Capture foi descartada: no Windows 10 desenha uma borda amarela que não dá para desligar. Na tela parada a DXGI repete o último quadro (senão quem entra vê tela preta). |
| **Redução com OpenCV `INTER_AREA`** | 13 ms de CPU/quadro (1080p → 720p), contra 40 ms do Pillow. Limitado a 2 threads para não roubar núcleos do encoder. Custo: +113 MB de instalação. |
| **Cursor desenhado via Win32** | Nem a DXGI nem o GDI incluem o cursor. |
| **Janela específica via Windows Graphics Capture** (`windows-capture`) | Pedido do usuário ("como o Go Live do Discord"). Captura a janela mesmo coberta, já com cursor. No Windows 10 desenha uma borda amarela que não dá para desligar; telas inteiras continuam na DXGI, sem borda. Usa `opencv-python` (não o `-headless`), que é o que ele exige. |
| **Padrões de qualidade no PC; `/transmitir` sobrescreve** | Decisão do usuário. O protocolo diz quais parâmetros foram digitados (`definidos`) e o PC devolve os aplicados. Trocar com a transmissão no ar republica só a track: a sessão e o link do espectador continuam. |
| **WebSocket iniciado pelo cliente** | Atravessa NAT sem abrir portas em casa. Heartbeat + reconexão com backoff exponencial. Contrato em [protocolo.md](protocolo.md). |
| **SDK importado na inicialização** | Carregar a lib nativa do LiveKit leva centenas de ms e trava o event loop. No cliente falso isso atrasou o `ack` além do prazo. |

Referência medida nesta máquina (tela 1920x1080): encode 720p30 VP8 = 3,6 ms/frame e 25 ms de latência
mediana; encode 1080p60 H.264 = 14,4 ms/frame (limite de 16,7 ms). Captura + redução: 720p30 = 60% de um
núcleo; 1080p60 nativo (sem redução) = 67%. Detalhes em [cliente_desktop/README.md](../cliente_desktop/README.md).

## SFU (`infra_sfu/`)

Veja [infra_sfu/README.md](../infra_sfu/README.md#decisões-e-evoluções). Em resumo: LiveKit
v1.13.9 + Caddy em `network_mode: host`, nó único, VP8/H.264 apenas, TLS via
Let's Encrypt, portas abertas na Security List **e** no iptables da VM.

## Espectadores (página servida pelo backend)

| Decisão | Motivo |
|---|---|
| **Link com chave por transmissão** | Escolha do usuário. Sem login, parecido com o "Go Live" do Discord: só quem está no canal vê o link. Chave de 128 bits que morre com a transmissão. Login com Discord pode ser somado depois sem refazer nada. |
| **Chave em texto puro no banco** | O bot posta o link no canal (o Discord guarda em claro) e o `/assistir` precisa mostrá-lo de novo. Hash não protegeria nada aqui. |
| **Página servida pelo próprio backend** | Mesma origem da API do token: sem CORS e um deploy só. A pasta `viewer_web/` foi removida. |
| **HTML sem token; token por POST** | Robôs de preview (Discord) e qualquer GET não ganham acesso ao vídeo. |
| **SDK pelo CDN com SRI + CSP + no-referrer** | Sem etapa de build. O SRI barra arquivo adulterado. O no-referrer impede que a chave, que está na URL, vaze para o CDN. |
| **Migrações por `PRAGMA user_version`** | A coluna da chave entrou num banco que já existia. As migrações ficam em `MIGRACOES` (repositorio.py) e nunca são editadas depois de publicadas. |
