# Protocolo backend ↔ cliente desktop (v1)

WebSocket com mensagens JSON (texto), uma por frame. Quem abre a conexão é **o cliente**,
por isso funciona atrás de NAT sem abrir portas em casa.

- Endpoint: `ws://127.0.0.1:8000/ws/cliente` (dev), `wss://api.seudominio.com/ws/cliente` (produção)
- Todo objeto tem o campo `tipo`. Campos desconhecidos são ignorados, então dá para evoluir sem quebrar.
- Implementação de referência: [backend_bot/app/protocolo.py](../backend_bot/app/protocolo.py).
  O cliente desktop precisa espelhar estes modelos.

## 1. Handshake (primeira mensagem, até 10 s depois de conectar)

Primeira vez: o usuário roda `/parear` no Discord e digita o código no cliente.

```json
→ {"tipo": "parear", "v": 1, "codigo": "ABC-234", "nome_dispositivo": "PC-Sala"}
← {"tipo": "pareado", "device_token": "<segredo>", "usuario": "Fulano", "dispositivo": "PC-Sala"}
```

O cliente **guarda o `device_token`** (ele só é enviado uma vez; o backend guarda apenas o hash).
Nas conexões seguintes:

```json
→ {"tipo": "hello", "v": 1, "device_token": "<segredo>", "versao_cliente": "0.1.1", "sessao_ativa": null}
← {"tipo": "bem_vindo", "usuario": "Fulano", "dispositivo": "PC-Sala"}
```

`sessao_ativa` é o id da transmissão que o cliente ainda mantém no ar (ou `null`). Ao reconectar,
o backend ajusta o próprio estado com base nisso: o cliente é quem sabe o que está transmitindo.

Código de pareamento: 6 caracteres sem ambíguos (sem `0 O 1 I`), hífen e caixa opcionais,
validade de 5 min, uso único. Parear de novo substitui o PC anterior (um PC por usuário).

## 2. Comandos (backend → cliente)

Todo comando tem um `id`. O cliente responde **`ack`** assim que recebe (prazo: 5 s) e
**`resultado`** quando termina (prazo: 20 s para `iniciar`). Se só o `resultado` chegar, ele vale
também como `ack`.

### `iniciar`

O `id` também é o id da sessão de transmissão.

```json
← {"tipo": "iniciar", "id": "9f2c…", "sala": "dev",
   "livekit_url": "wss://live.seudominio.com", "token": "<jwt do LiveKit>",
   "video": {"largura": 1280, "altura": 720, "fps": 30, "bitrate_kbps": 2500, "codec": "vp8"}}
→ {"tipo": "ack", "id": "9f2c…"}
→ {"tipo": "resultado", "id": "9f2c…", "ok": true, "detalhe": ""}
```

**Quem decide os parâmetros:** `definidos` lista o que a pessoa digitou no `/transmitir`
(`resolucao`, `fps`, `bitrate_kbps`, `codec`). O resto, o PC pode trocar pelos padrões dele (escolhidos
na janela do app). Sem o campo (backend antigo), vale tudo o que veio. O `resultado` devolve em `video`
o que foi **realmente aplicado**, e é isso que o bot mostra no Discord:

```json
← {"tipo": "iniciar", ..., "definidos": ["fps"]}
→ {"tipo": "resultado", "id": "9f2c…", "ok": true,
   "video": {"largura": 854, "altura": 480, "fps": 60, "bitrate_kbps": 900, "codec": "vp8"}}
```

O token do LiveKit vale 15 min **só para entrar** (depois o SFU o renova sozinho) e só permite
publicar fontes `screen_share` / `screen_share_audio` na sala indicada. Identidade no LiveKit: `pc-<discord_id>`.

### `parar`

```json
← {"tipo": "parar", "id": "41ab…", "sessao_id": "9f2c…"}
→ {"tipo": "ack", "id": "41ab…"}
→ {"tipo": "resultado", "id": "41ab…", "ok": true, "detalhe": ""}
```

## 3. Eventos espontâneos (cliente → backend)

O usuário parou pela bandeja, o app fechou, a captura falhou:

```json
→ {"tipo": "transmissao_encerrada", "sessao_id": "9f2c…", "motivo": "parado pela bandeja"}
```

O usuário trocou fonte ou qualidade no PC com a transmissão no ar (mesma sessão, mesmo link):

```json
→ {"tipo": "transmissao_atualizada", "sessao_id": "9f2c…",
   "video": {"largura": 1920, "altura": 1080, "fps": 60, "bitrate_kbps": 6000, "codec": "vp8"}}
```

## 4. Erros e fechamento

Antes de fechar, o backend manda `{"tipo": "erro", "codigo": "...", "mensagem": "..."}`.
Uma mensagem inválida *depois* do handshake gera só o `erro`, sem fechar a conexão.

| Código WS | Quando | O que o cliente faz |
|---|---|---|
| 1012 | backend reiniciando (deploy, restart) | reconectar com backoff (o padrão abaixo) |
| 4000 | handshake malformado | bug: logar e não reconectar em loop |
| 4001 | código/token inválido ou handshake atrasado | apagar o token salvo e pedir novo pareamento |
| 4002 | o mesmo PC conectou em outro lugar | **não** reconectar (evita duas instâncias brigando) |
| 4003 | versão do protocolo incompatível | avisar o usuário para atualizar o cliente |
| outros | queda de rede, backend reiniciando | reconectar com backoff exponencial (1 s → 30 s) |

## 5. Keepalive

O servidor manda ping de WebSocket a cada 20 s (Hypercorn). O cliente também deve pingar
(o padrão da biblioteca `websockets` já faz isso) para detectar quedas e manter o NAT aberto.
