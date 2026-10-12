# Pendências

Atualizado a cada etapa. **[você]** = ação manual sua; **[código]** = trabalho de implementação.

## Colocar no ar (em andamento)

Decidido: conta OCI nova (região São Paulo), **DuckDNS** (um nome por VM), tudo **isolado** no compartimento
`live-dos-dude` (a conta é compartilhada com outros sistemas), instalação feita por mim via CLI/SSH.

Já criado na OCI, tudo no compartimento `live-dos-dude` e com a etiqueta `projeto=live-dos-dude`:
- [x] Compartimento + **cotas**: Ampere ≤ 2 OCPU / 12 GB, Micro ≤ 1, IPs reservados ≤ 2, **zero** para os 63 formatos pagos
- [x] Rede própria `live-dos-dude-vcn` (10.77.0.0/16): subnet pública, gateway, rotas; a subnet só libera SSH
- [x] Firewalls por VM (NSG): `nsg-live-sfu` (80, 443, 7881, UDP 50000-60000) e `nsg-live-api` (80, 443)
- [x] VM `live-api` (E2.1.Micro, Ubuntu 24.04) com IP reservado **132.226.162.91**; setup feito (iptables, swap 2 GB, Docker)
- [x] **Backend no ar** em <https://livedosdude-api.duckdns.org> (HTTPS do Let's Encrypt, WebSocket testado pela internet), por enquanto **sem o bot**
- [x] Chaves de produção do LiveKit geradas e guardadas no `.env` da `live-api` (vão também para a `live-sfu`)
- [ ] VM `live-sfu` (Ampere): **sem capacidade em São Paulo** em nenhum fault domain, nem com 1 OCPU/6 GB; um script tenta a cada 5 min (12 h)
- [x] **Ponte: LiveKit Cloud** (plano grátis, projeto `live-dos-dudes`): smoke test OK (RTT 10 ms, 36 ms de latência), backend da nuvem apontando para ele, **webhook chegando e validado**. As chaves da OCI estão guardadas no `.env` da VM para a volta
- [ ] **[código]** Quando a Ampere sair: subir o LiveKit na `live-sfu` e trocar o backend de volta (as chaves `OCI_` já estão no `.env`)
- [x] Chave SSH `~/.ssh/live_dos_dude`; `~/.ssh/config` com o apelido `live-api`

O que falta de você:
- [x] DuckDNS: `livedosdude-api` → 132.226.162.91; `livedosdude-live` criado (aponta para a sua casa até a VM sair)
- [x] Bot adicionado ao **Servidor de teste** (`1559297664166797322`) e **rodando na nuvem** (`lives#6877`, 5 comandos)
- [x] Bot também no servidor real **Bom de Praia 🏖** (`1115479257787924561`); `DISCORD_GUILD_ID` aceita vários IDs (os dois estão configurados)
- [x] Canal-guia `#live-dos-dude` criado nos **dois** servidores (Bom de Praia e Servidor de teste), atualizado sozinho a cada início do bot
- [x] **Teste de ponta a ponta na nuvem OK** (2026-10-12): `/parear` → cliente desktop → `/transmitir` → transmissão assistida pelo link
- [ ] **[você]** Quando a `live-sfu` sair: trocar o IP do `livedosdude-live` no site do DuckDNS (eu aviso qual)
- [ ] **[você]** Com o bot na nuvem, **não rode o backend local com o mesmo token** (os dois responderiam os comandos)
- [ ] **[você]** Depois do deploy: **revogar a chave de API** da OCI (perfil → *Tokens and keys* → ⋮ → *Delete*). Ela é do seu usuário administrador e enxerga a conta toda, não só o projeto

## Infra SFU (OCI)

Passo a passo em [infra_sfu/README.md](infra_sfu/README.md#2-deploy-na-oci).

- [ ] **[você]** Criar a VM Ampere A1 (Ubuntu 24.04) com IP público **reservado** (2.1)
- [ ] **[você]** Security List: TCP 80, 443, 7881 e UDP 50000-60000 (2.2)
- [ ] **[você]** Domínio próprio ou DuckDNS: registro A `live.<domínio>` → IP da VM (2.3)
- [ ] **[você]** Copiar arquivos, rodar `setup_host.sh`, gerar chaves, preencher `.env`, `docker compose up -d` (2.4 a 2.6)
- [ ] **[você]** `smoke_test.py --url wss://...` do seu PC: conferir transporte **UDP** e candidato **host** (2.7)

## Backend

- [ ] **[você]** Criar a aplicação do bot no Discord e preencher `backend_bot/.env` ([passo a passo](backend_bot/README.md#criar-o-bot-no-discord))
- [ ] **[você]** Teste manual com Discord real + cliente falso publicando no LiveKit ([roteiro](backend_bot/README.md#testar-o-fluxo-inteiro-antes-de-o-cliente-desktop-existir))
- [ ] **[você]** Decidir se `/transmitir` fica aberto a todos ou restrito a um cargo (`CARGO_TRANSMISSOR`)

### Deploy do backend (VM separada do LiveKit)

Passo a passo em [backend_bot/README.md](backend_bot/README.md#deploy-vm-própria-separada-da-do-livekit). O código está pronto e validado localmente.

- [ ] **[você]** Criar a 2ª VM: **E2.1.Micro** (x86, 1 GB, Always Free), Ubuntu 24.04, IP reservado (passo 1)
- [ ] **[você]** Security List da VM do backend: só TCP 80 e 443 (passo 1)
- [ ] **[você]** DNS: registro A `api.<domínio>` → IP da VM do backend (passo 1)
- [ ] **[você]** Copiar arquivos, `setup_host.sh backend`, preencher `.env` e `docker compose up -d --build` (passos 2 a 4)
- [ ] **[você]** Ativar o webhook no `livekit.yaml` da VM do LiveKit (passo 5)
- [ ] **[você]** Agendar o backup diário no cron (passo 6)
- [ ] **[código]** (evolução) Copiar os backups para fora da VM (ex.: Object Storage da OCI); hoje ficam no mesmo volume
- [ ] **[código]** (evolução) Mais de um PC por usuário. Hoje, parear de novo substitui o anterior
- [ ] **[código]** (evolução) `/sala configurar` para mudar os padrões. Hoje toda sala nasce com 720p · 30 fps · 2500 kbps · VP8

## Cliente desktop

- [ ] **[você]** Nesta máquina já existe outro serviço na porta 8000 (um `WSGIServer` Python 3.12 via WSL/Docker). Para testar webhooks localmente, rode o backend com `PORTA=8010` (é para onde o LiveKit de dev manda)
- [ ] **[você]** Teste manual com o Discord real: `/parear` → digitar o código na janela → `/transmitir` → assistir → parar pela bandeja ([como rodar](cliente_desktop/README.md#rodar))
- [ ] **[você]** Atualizar o Python para a última 3.13.x: o 3.13.0 tem um bug do Tk dentro de venvs (há um contorno no código, mas é melhor não depender dele)
- [ ] **[você]** Se usar escala de tela 125%/150%, conferir se o cursor aparece no lugar certo (só testei em 100%)
- [ ] **[você]** Subir o `dist/LiveDosDude-0.1.0-windows.zip` (76 MB) num Drive e mandar o link para quem vai transmitir
- [ ] **[código]** (evolução) Assinatura digital do `.exe` (tira o aviso do SmartScreen; certificado é pago)
- [ ] **[código]** (evolução) Instalador com atalho no Menu Iniciar e desinstalador (ex.: Inno Setup)
- [ ] **[você]** Testar o seletor: transmitir uma **janela** (ex.: um jogo ou o navegador) e trocar a qualidade com **Aplicar agora** no meio
- [ ] **[código]** (evolução) Áudio do sistema junto com a tela (o token já permite `screen_share_audio`)

## Página do espectador

- [ ] **[você]** No deploy, conferir `URL_PUBLICA=https://api.<domínio>` no `.env` do backend (é a base dos links que o bot posta)
- [ ] **[você]** Abrir um link no celular e conferir o layout (o CSS tem regras para telas pequenas, mas só testei no desktop)
- [ ] **[código]** (evolução) Login com Discord como opção extra de acesso
- [ ] **[código]** (evolução) Áudio da transmissão na página (quando o cliente publicar áudio)

## Feito

- [x] 2026-10-12: **Executável do cliente** (PyInstaller, pasta + zip de 76 MB): já aponta para o backend de produção, opção "Iniciar com o Windows", `--diagnostico`; testado de ponta a ponta transmitindo uma janela
- [x] 2026-10-12: **Fonte e qualidade na janela do cliente**: seletor de tela/janela estilo Go Live, resolução/FPS/bitrate, `/transmitir` com prioridade, "Aplicar agora" sem trocar o link; backend na VM atualizado; 39 + 64 testes
- [x] 2026-10-12: **Sistema no ar**: bot e backend na OCI (`livedosdude-api.duckdns.org`), vídeo pelo LiveKit Cloud (ponte), primeira transmissão real assistida pelo link
- [x] 2026-10-12: Revisão da arquitetura e decisões registradas em [docs/arquitetura.md](docs/arquitetura.md)
- [x] 2026-10-12: Infra SFU: configs validados com as imagens reais, `setup_host.sh` testado num Ubuntu simulado, smoke test local OK (encode por CPU confirmado)
- [x] 2026-10-12: Backend: bot + API + SQLite + protocolo v1; 40 testes, incluindo publicação real no LiveKit local
- [x] 2026-10-12: Decidido: backend numa VM separada da do LiveKit
- [x] 2026-10-12: Cliente desktop: bandeja + janela + captura DXGI + cursor + LiveKit; 31 testes, incluindo o app real contra o backend real com o vídeo chegando a um espectador
- [x] 2026-10-12: Corrigido no backend: desligar com PCs conectados travava para sempre (agora fecha com 1012 e os clientes reconectam)
- [x] 2026-10-12: Decidido: espectador entra por link com chave (sem login)
- [x] 2026-10-12: Deploy do backend pronto: imagem (x86 e ARM), compose com Caddy, `setup_host.sh` com perfis, webhook assinado, parada forçada pelo LiveKit e backup; validado localmente
- [x] 2026-10-12: Página do espectador: link com chave por transmissão, servida pelo backend; validada num navegador real (vídeo tocando, link inválido, fim da transmissão)
