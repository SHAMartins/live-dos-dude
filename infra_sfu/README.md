# Infra SFU (LiveKit)

Servidor que recebe o vídeo do cliente desktop e distribui para os espectadores.
Não transcodifica nada: só repassa pacotes. Por isso usa pouca CPU, e o custo
real é a banda de saída.

```
                      sinalização (wss, TLS)
PC / espectadores ──── TCP 443 ────► Caddy ──► LiveKit :7880 (só localhost)
                  ──── UDP 50000-60000 ─────► LiveKit   mídia, direto, sem proxy
                  ──── TCP 7881 ────────────► LiveKit   fallback se UDP bloqueado
```

| Arquivo | Para quê |
|---|---|
| `docker-compose.yml` | Produção: LiveKit + Caddy, ambos em `network_mode: host` |
| `livekit.yaml` | Config de produção (sem segredos) |
| `Caddyfile` | TLS automático (Let's Encrypt) e proxy da sinalização |
| `.env.example` | Domínio, e-mail do ACME e chaves da API |
| `scripts/setup_host.sh` | Prepara a VM: iptables da OCI, buffers UDP, Docker |
| `dev/` | LiveKit local (Docker Desktop) para desenvolver sem a OCI |
| `tools/smoke_test.py` | Publica vídeo pelo SFU, mede latência e confirma o encoder por software |
| `tools/gerar_token.py` | Gera tokens para testes manuais |

---

## 1. Testar localmente

No PowerShell, a partir da raiz do projeto:

```bash
docker compose -f infra_sfu/dev/docker-compose.yml up -d
```

```bash
python -m venv infra_sfu/tools/.venv
```

```bash
infra_sfu/tools/.venv/Scripts/python -m pip install -r infra_sfu/tools/requirements.txt
```

```bash
infra_sfu/tools/.venv/Scripts/python infra_sfu/tools/smoke_test.py
```

Saída esperada (medida nesta máquina, 720p30 VP8):

```
  recebido: 1280x720 @ 30.1 fps (alvo 30)
  encoder: libvpx -> software (CPU)
  encode médio: 3.6 ms/frame | enviado 1280x720 @ 30 fps | alvo 2500 kbps
  latência captura->decodificado: mediana 25 ms | p95 27 ms
RESULTADO: OK
```

Referência para o cliente desktop: 1080p60 em H.264 (OpenH264) custou 14,4 ms/frame,
perto do limite de 16,7 ms que 60 fps permitem.

---

## 2. Deploy na OCI

### 2.1 Criar a VM

- **Shape:** `VM.Standard.A1.Flex` (Ampere/ARM, Always Free até 4 OCPU / 24 GB). 2 OCPU / 12 GB sobram.
  Se der *Out of capacity*, tente outro Availability Domain ou outro horário.
- **Imagem:** Canonical Ubuntu 24.04 (aarch64). A imagem do LiveKit tem build arm64.
- **Região:** a mais próxima dos espectadores (ex.: São Paulo ou Vinhedo). Menos distância, menos latência.
- **IP público reservado:** *Networking → IP Management → Reserved Public IPs*, e associe à VNIC da VM.
  Sem isso o IP muda quando a VM é parada, e o DNS quebra.

### 2.2 Liberar portas na Security List

*Networking → Virtual Cloud Networks → sua VCN → Security Lists → Default → Add Ingress Rules*
(stateful, origem `0.0.0.0/0`):

| Protocolo | Porta | Uso |
|---|---|---|
| TCP | 80 | emissão/renovação do certificado (ACME) |
| TCP | 443 | sinalização `wss://` |
| TCP | 7881 | ICE sobre TCP |
| UDP | 50000-60000 | mídia |

**Não** abra a 7880. Ela só é acessada pelo Caddy, dentro da VM.

> A Security List não basta: a imagem Ubuntu da Oracle tem um iptables que rejeita
> tudo menos SSH. O `setup_host.sh` (passo 2.4) resolve isso.

### 2.3 DNS

Crie um registro **A** `live.seudominio.com` apontando para o IP reservado. Sem domínio
próprio, um subdomínio gratuito do [DuckDNS](https://www.duckdns.org) funciona.

Confira antes de subir o Caddy (senão o Let's Encrypt falha):

```bash
nslookup live.seudominio.com
```

### 2.4 Copiar os arquivos e preparar a VM

Do seu PC (o OpenSSH já vem no Windows 10):

```bash
ssh ubuntu@IP_DA_VM mkdir -p infra_sfu
```

```bash
scp -r infra_sfu/docker-compose.yml infra_sfu/livekit.yaml infra_sfu/Caddyfile infra_sfu/.env.example infra_sfu/scripts ubuntu@IP_DA_VM:~/infra_sfu/
```

Na VM:

```bash
cd ~/infra_sfu && sudo bash scripts/setup_host.sh
```

Saia e entre de novo no SSH para o grupo `docker` valer.

### 2.5 Chaves e `.env`

```bash
docker run --rm livekit/livekit-server:v1.13.9 generate-keys
```

```bash
cp .env.example .env && chmod 600 .env && nano .env
```

Guarde a API key/secret: o backend vai usar o mesmo par para assinar tokens.

### 2.6 Subir

```bash
docker compose up -d && docker compose logs -f
```

O que procurar nos logs:
- `livekit`: `found external IP via STUN` com o IP público, **sem** `could not validate external IP`;
- `caddy`: `certificate obtained successfully`.

```bash
curl https://live.seudominio.com
```

A resposta deve ser `OK`.

### 2.7 Validar do seu PC

```bash
infra_sfu/tools/.venv/Scripts/python infra_sfu/tools/smoke_test.py --url wss://live.seudominio.com --api-key SUA_KEY --api-secret SEU_SECRET
```

(ou defina `LIVEKIT_URL`, `LIVEKIT_API_KEY` e `LIVEKIT_API_SECRET` e rode sem flags)

Na OCI o esperado é `transporte: UDP, SFU <ip-publico>:5xxxx (host)`. Se aparecer
`TCP`, a mídia funciona, mas o UDP está bloqueado em algum ponto e a latência piora.

Para **assistir no navegador**, rode com `--manter` e cole a URL e o token impressos
na aba *Custom* de <https://meet.livekit.io>.

---

## Problemas comuns

| Sintoma | Causa provável | Correção |
|---|---|---|
| `curl` dá timeout na 443 | porta fechada na Security List ou no iptables | passos 2.2 e 2.4 |
| Caddy não emite certificado | DNS ainda não propagou ou porta 80 fechada | `nslookup`, liberar TCP 80 |
| `401 Unauthorized` no smoke test | key/secret diferentes do `.env` da VM | conferir `.env`, `docker compose up -d` |
| Etapa 3 falha / log `could not validate external IP` | UDP 50000-60000 não chega na VM | Security List + `sudo iptables -L INPUT -n` |
| Transporte `TCP` no relatório | rede do cliente bloqueia UDP | funciona, com mais latência; veja "Evoluções" |
| Parou de funcionar após reiniciar a VM | IP público efêmero mudou | usar IP reservado (2.1) |

## Atualizar o LiveKit

Troque a tag em `docker-compose.yml` (fixada de propósito, nada de `latest`) e rode:

```bash
docker compose pull && docker compose up -d
```

## Dimensionamento

Banda de saída ≈ bitrate × espectadores. Exemplo: 720p30 a 2,5 Mbps para 10
espectadores dá ~25 Mbps. O Always Free da OCI inclui 10 TB/mês de saída.

## Decisões e evoluções

- **Nó único, sem Redis:** Redis só é necessário com vários nós do LiveKit.
- **Sem TURN embutido:** ICE/TCP na 7881 já cobre redes sem UDP. TURN/TLS na 443 só faz
  falta para espectadores em redes corporativas que liberam apenas 443. Se precisar,
  adiciona-se um subdomínio de TURN com roteamento por SNI no Caddy.
- **Codecs limitados a VP8/H.264:** são os que o cliente codifica por software. VP9/AV1 pesariam na CPU.
- **Webhooks** (stream começou/terminou, espectador entrou) entram na fase do backend.
  O bloco já está comentado no `livekit.yaml`.
