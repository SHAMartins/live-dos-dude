# Backend (bot do Discord + API de sinalização)

Um processo, um event loop: o bot (discord.py) e a API (Quart/Hypercorn) rodam juntos.
O bot chama o serviço direto, e o serviço empurra comandos para o PC pelo WebSocket
que o próprio cliente desktop abriu.

```
/transmitir ─► bot/comandos.py ─► services/transmissao.py ─► services/gateway.py ─► WebSocket ─► PC
                (só Discord)       (regras, SQLite, tokens)    (ack/resultado, prazos)
```

O protocolo do WebSocket está em [docs/protocolo.md](../docs/protocolo.md).

## Rodar localmente

No PowerShell, dentro de `backend_bot/`:

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

Testes (59, incluindo dois de ponta a ponta com o LiveKit local, que são pulados se ele não estiver no ar):

```bash
.venv/Scripts/python -m pytest
```

Subir o backend (sem `.env`, sobe só a API, apontando para o LiveKit local):

```bash
.venv/Scripts/python -m app
```

## Criar o bot no Discord

1. <https://discord.com/developers/applications> → **New Application**.
2. Aba **Bot** → **Reset Token** → copie para `DISCORD_TOKEN` no `.env` (`cp .env.example .env`).
   Nenhum *Privileged Gateway Intent* é necessário: deixe todos desligados.
3. Aba **OAuth2 → URL Generator**: escopos `bot` e `applications.commands`; permissões `View Channels`,
   `Send Messages`, `Embed Links`, `Manage Channels` (para criar o `#live-dos-dude`) e `Read Message History`.
   Abra a URL gerada e adicione o bot ao seu servidor.
4. No Discord: *Configurações → Avançado → Modo desenvolvedor*. Clique direito no servidor →
   **Copiar ID** → `DISCORD_GUILD_ID` no `.env`. Assim os comandos aparecem na hora.
5. `.venv/Scripts/python -m app`: o log deve mostrar `5 comandos registrados no servidor ...`.

## Testar o fluxo inteiro antes de o cliente desktop existir

Com o LiveKit local e o backend no ar, o cliente falso faz o papel do PC e publica um
vídeo colorido de verdade no LiveKit:

1. No Discord: `/parear` e copie o código.
2. Em outro terminal (dentro de `backend_bot/`):

   ```bash
   .venv/Scripts/python tools/cliente_falso.py --parear ABC-234 --publicar
   ```

3. No Discord: `/transmitir sala:teste`. Em ~1 s deve aparecer a mensagem 🔴.
4. Abra o link **Assistir** que o bot postou: a página mostra a tela piscando.
5. `/parar`: a página avisa que a transmissão terminou e o link para de funcionar.

Depois do primeiro pareamento, rode o cliente falso sem `--parear`: ele reconecta com o token salvo.
Para simular problemas: `--modo mudo` (PC travado) e `--modo falhar` (erro ao iniciar).

## Página do espectador

Cada `/transmitir` gera uma chave aleatória de 128 bits, e o bot posta `URL_PUBLICA/assistir/<chave>`.
Quem tem o link assiste no navegador, sem login e sem instalar nada. A chave para de funcionar quando a
transmissão acaba. Se várias pessoas transmitirem na mesma sala, a página mostra todas lado a lado.

- O HTML não carrega token: o preview de link do Discord vê só o título ("🔴 Fulano ao vivo na sala dev").
  O token, só de assistir e só daquela sala, sai num `POST` feito pelo JavaScript da página.
- O SDK do navegador (`livekit-client` 2.22.3) vem do jsDelivr com **SRI**: se o arquivo mudar, o navegador recusa.
- CSP restrita (só scripts próprios e do CDN; conexões só com o backend e o LiveKit) e `Referrer-Policy: no-referrer`,
  porque a chave está na URL e não pode vazar para o CDN.

## Canal de ajuda `#live-dos-dude`

Quando entra no servidor (e toda vez que liga), o bot cria o canal `#live-dos-dude`, somente leitura para
os membros, com um guia: o que é, como assistir, como começar a transmitir e cada comando. Se o canal já
existe, ele só atualiza a mensagem, então o guia acompanha as mudanças nos comandos. O texto fica em
[app/bot/guia.py](app/bot/guia.py), e um teste garante que todo comando registrado aparece nele.

## Comandos

| Comando | O que faz | Resposta |
|---|---|---|
| `/parear` | Gera código de 6 caracteres (5 min, uso único) | só para quem pediu |
| `/transmitir sala [resolucao] [fps] [bitrate] [codec]` | Manda o PC iniciar; parâmetros omitidos vêm da sala. Posta o link de espectador | erros rápidos só para quem pediu; o resultado (com o link) aparece para todos |
| `/parar` | Manda o PC parar | todos |
| `/status` | PC online/offline e transmissão atual | só para quem pediu |
| `/assistir sala` | Links das transmissões ativas na sala | só para quem pediu |

`/transmitir` exige o cargo de `CARGO_TRANSMISSOR` (se configurado) e só aciona **o PC de quem
digitou**. Ninguém consegue ligar a tela de outra pessoa.

## Deploy (VM própria, separada da do LiveKit)

Arquivos em [deploy/](deploy/): compose (backend + Caddy), Caddyfile e `.env.example`. Imagem:
[Dockerfile](Dockerfile) (`python:3.13.16-slim`, x86 e ARM, usuário sem privilégios, healthcheck em `/saude`).

**1. Na OCI:** VM **E2.1.Micro** (x86, 1 GB, Always Free) com Ubuntu 24.04 e IP público **reservado**.
Na Security List, libere só TCP 80 e 443. No DNS, crie o registro A `api.<domínio>` apontando para o IP.

**2. Copiar** (do seu PC, na raiz do projeto):

```bash
ssh ubuntu@IP_DO_BACKEND mkdir -p backend
```

```bash
scp -r backend_bot/app backend_bot/requirements.txt backend_bot/Dockerfile backend_bot/.dockerignore backend_bot/deploy infra_sfu/scripts/setup_host.sh ubuntu@IP_DO_BACKEND:~/backend/
```

**3. Preparar a VM** (iptables da Oracle + Docker), e depois sair e entrar de novo no SSH:

```bash
sudo bash ~/backend/setup_host.sh backend
```

**4. Configurar e subir:**

```bash
cd ~/backend/deploy && cp .env.example .env && chmod 600 .env && nano .env
```

```bash
docker compose up -d --build && docker compose logs -f
```

```bash
curl https://api.seudominio.com/saude
```

O log deve mostrar `5 comandos registrados no servidor ...` e o `curl` deve responder `"ok": true`.

**5. Webhook:** na VM do LiveKit, descomente o bloco `webhook` em `infra_sfu/livekit.yaml` (com a API
key e a URL `https://api.<domínio>/livekit/webhook`) e rode `docker compose up -d`.

**6. Backup diário** (`crontab -e` na VM do backend; mantém os 14 últimos dentro do volume):

```
30 4 * * * cd ~/backend/deploy && docker compose exec -T backend python -m app.backup >> ~/backup.log 2>&1
```

**7. Cliente desktop:** em *Configurações*, endereço do backend `wss://api.<domínio>/ws/cliente`.

**Atualizar:** copie os arquivos de novo (passo 2) e rode `docker compose up -d --build`. O desligamento é
gracioso: os PCs recebem o fechamento 1012 e reconectam sozinhos quando o backend volta.

## Webhook do LiveKit e parada forçada

- `POST /livekit/webhook` exige a assinatura do LiveKit (JWT com o SHA-256 do corpo, assinado com o
  API secret). Sem ela: 401.
- **Espectadores:** contados por sala e mostrados no `/status`.
- **PC caiu do LiveKit** sem avisar o backend: a sessão é encerrada. Saídas por reconexão
  (`DUPLICATE_IDENTITY`) e eventos atrasados de conexões anteriores são ignorados.
- **`/parar` sem resposta do PC:** o backend tira o PC da sala pela API do LiveKit, porque ele pode ter
  perdido só o WebSocket e continuar transmitindo.

## Estrutura

| Caminho | Responsabilidade |
|---|---|
| `app/__main__.py` | Sobe bot + Hypercorn no mesmo loop (`TaskGroup`: se um cai, o outro cai junto) |
| `app/config.py` | Configuração via `.env` (pydantic-settings) |
| `app/protocolo.py` | Modelos das mensagens do WebSocket |
| `app/api/` | `/saude` e `/ws/cliente` (handshake, recepção, envio) |
| `app/services/gateway.py` | Conexões online; comando → `ack` → `resultado` com prazo |
| `app/services/transmissao.py` | Regras: pareamento, transmitir/parar, reconciliação ao reconectar |
| `app/services/tokens.py` | JWTs do LiveKit (publicador só `screen_share`; espectador só assiste) |
| `app/db/` | SQLite via aiosqlite (WAL): usuários, PCs (hash do token), códigos, salas, sessões |
| `app/bot/` | Slash commands; só traduzem Discord ↔ serviço |
| `app/api/espectador.py` + `app/web/` | Página do espectador (`/assistir/<chave>`) e emissão do token (`POST /api/assistir/<chave>`) |
| `app/api/webhook.py` + `app/services/livekit_admin.py` | Webhook assinado do LiveKit; remoção de participante pela API |
| `app/backup.py` | Backup consistente do SQLite com o backend rodando |
| `deploy/` + `Dockerfile` | Produção: imagem, compose com Caddy, `.env.example` |
| `tools/cliente_falso.py` | PC simulado, para testes manuais e automáticos |
