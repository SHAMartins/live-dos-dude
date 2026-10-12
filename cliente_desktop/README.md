# Cliente desktop

Fica na bandeja do Windows, conectado ao backend. Quando chega o `/transmitir`, captura a
tela, codifica **só na CPU** e publica no LiveKit. Protocolo: [docs/protocolo.md](../docs/protocolo.md).

```
thread principal  janela (customtkinter)  ── lê o PainelEstado a cada 250 ms
thread "bandeja"  ícone (pystray)         ── cliques viram pedidos para as outras threads
thread "asyncio"  Controlador             ── WebSocket do backend + sala do LiveKit
thread "midia"    ThreadMidia             ── captura → reduz → cursor → encoder, no fps pedido
```

## Rodar

No PowerShell, dentro de `cliente_desktop/`:

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

```bash
.venv/Scripts/python -m cliente
```

Na primeira vez a janela abre pedindo o código: rode `/parear` no Discord e digite-o. Depois
disso o app abre direto na bandeja e reconecta sozinho. Fechar a janela **não** encerra o
app; para sair, use *Sair* na janela ou no menu da bandeja.

Arquivos em `%APPDATA%\LiveDosDude\`: `config.json` (endereço do backend, nome do PC, monitor)
e `cliente.log`. O token do PC fica no **Gerenciador de Credenciais do Windows**, nunca em arquivo.

## Executável para distribuir

```bash
.venv/Scripts/python -m pip install -r requirements-build.txt
```

```bash
.venv/Scripts/python empacotamento/empacotar.py
```

Gera `dist/LiveDosDude/LiveDosDude.exe` (pasta de ~190 MB), `dist/LiveDosDude-<versão>-windows.zip`
(~76 MB, versão portátil) e, se o [Inno Setup 6](https://jrsoftware.org/isinfo.php) estiver instalado
(`winget install --id JRSoftware.InnoSetup -e`), **`dist/LiveDosDude-<versão>-instalador.exe`** (~53 MB),
que é o que se manda para os amigos. É uma pasta e não um `.exe` único de propósito: o arquivo único
descompactaria ~200 MB a cada abertura; a pasta abre em ~1 s.

- Já vem apontando para `wss://livedosdude-api.duckdns.org`: o amigo instala, abre e só digita o código
  do `/parear`.
- **Instalador** ([empacotamento/instalador.iss](empacotamento/instalador.iss)): instala só para o usuário,
  sem pedir administrador, em `%LOCALAPPDATA%\Programs\LiveDosDude`. Cria o atalho no Menu Iniciar e
  oferece atalho na área de trabalho e "Iniciar com o Windows". Instalar uma versão nova por cima atualiza
  a anterior. Se o app estiver aberto, pede para fechá-lo.
- **Desinstalar** por *Configurações → Aplicativos → Live dos Dude*: tira o app, os atalhos e o
  "Iniciar com o Windows", e pergunta se apaga também as configurações e o pareamento (padrão: não, para
  reinstalar sem `/parear`).
- **Aviso do Windows na primeira vez** ("O Windows protegeu o computador"): o executável não tem
  assinatura digital (certificado pago). É só clicar em *Mais informações → Executar assim mesmo*.
- **Iniciar com o Windows:** caixa na seção *Conexão* da janela (só no executável; grava em
  `HKCU\...\Run`, sem precisar de administrador).
- **`LiveDosDude.exe --diagnostico`** confere LiveKit, Tk, cofre de credenciais, capturas, bandeja e som e
  grava o resultado em `%APPDATA%\LiveDosDude\diagnostico.txt`. É a primeira coisa a pedir se algo não
  funcionar no PC de um amigo.
- O Discord não aceita anexos desse tamanho na conta grátis: distribua por Google Drive, OneDrive ou similar.

## O que transmitir e com qual qualidade

Na janela, a seção **Transmissão** guarda os padrões deste PC:

- **Fonte:** *Escolher…* abre um seletor com miniaturas (como o Go Live do Discord). Na aba **Telas**, um
  monitor inteiro (captura DXGI). Na aba **Aplicativos**, uma janela específica, capturada mesmo coberta por
  outras (Windows Graphics Capture, cursor incluído). A janela é reencontrada pelo programa + título
  quando o app reinicia.
- **Resolução** (480p, 720p, 1080p ou *Nativa*), **FPS** (15, 30, 60) e **bitrate** (500–8000 kbps, com sugestão).
- **Prioridade:** o que for digitado no `/transmitir` vale naquela vez; o resto vem daqui. O Discord
  mostra os valores realmente usados.
- **Com a transmissão no ar**, mudar algo mostra **Aplicar agora**: a track é republicada (~1 s de vídeo
  parado) e **o link dos espectadores continua o mesmo**.

Limitações da captura de janela (são da API do Windows; valem igualmente para o Discord):
no Windows 10 uma **borda amarela** aparece em volta da janela, só na sua tela; janela **minimizada**
congela o vídeo até ser restaurada (o app avisa); janela **fechada** encerra a transmissão; conteúdo com
**DRM** e alguns jogos com anti-cheat aparecem pretos; redimensionar a janela coloca faixas pretas em vez
de distorcer.

## Testes

```bash
.venv/Scripts/python -m pytest
```

45 testes. Os de integração sobem o **backend real** (`../backend_bot`). Os marcados como `tela`
capturam a sua tela, e o ponta a ponta (`livekit`) precisa do LiveKit local
(`docker compose -f infra_sfu/dev/docker-compose.yml up -d`). Sem ele, é pulado.

## Decisões da mídia (todas medidas nesta máquina, tela 1920x1080)

| Etapa | Escolha | Por quê |
|---|---|---|
| Captura | **DXGI Desktop Duplication** (dxcam), com GDI (mss) de reserva | DXGI: ~3 ms/quadro. GDI: ~20 ms, limitado a ~45 fps. A Graphics Capture foi descartada: no Windows 10 desenha uma borda amarela que não dá para desligar e traz 113 MB de OpenCV junto |
| Tela parada | A DXGI repete o último quadro | Sem quadros novos o encoder para, e quem entra na sala vê tela preta até algo mudar |
| Redução | OpenCV `INTER_LINEAR`; reduções > 2x passam antes por uma metade exata (2:1), 2 threads | Era `INTER_AREA` (20-23 ms de CPU por quadro em 1080p→720p, o maior gasto do app). O LINEAR custa 5-9 ms com PSNR de 46 dB contra o AREA (imperceptível); em 1080p→480p, 2:1 + LINEAR dá 40,5 dB. Pillow BILINEAR custava 40 ms |
| Cursor | Desenhado por cima via Win32 (`DrawIconEx` sobre fundo preto e branco) | Nem a DXGI nem o GDI incluem o cursor. Suporta sombra (alfa) e cursores que invertem a cor (I-beam) |
| Encode | `ENCODER_BACKEND_SOFTWARE`, sem simulcast | Restrição do projeto. O teste ponta a ponta confirma `libvpx` e `power_efficient_encoder=false` |
| Entrega | `VideoFrame` de um array 1D | Com array 3D o SDK copia o quadro inteiro de novo |
| Som | WASAPI **loopback por processo** excluindo a árvore do Discord, PCM 48 kHz estéreo, Opus 128 kbps sem DTX | Todo o som do PC menos as vozes da call (como o Go Live do Discord). O Discord é procurado a cada 2 s; se abrir/fechar no meio, a captura é refeita. Precisa do Windows 10 2004+; sem isso a transmissão segue sem som. Discord no navegador não dá para separar |

### CPU total do app em jogo (0.1.1)

Medido nesta máquina (i5-10400F, 6 núcleos / 12 threads), com a tela em movimento constante (cena com rolagem
e personagens, parecida com jogo), VP8 por software, um espectador, **processo inteiro** (captura + redução +
conversão + encoder + rede). 100% = um núcleo lógico inteiro.

| Configuração | 0.1.0 (INTER_AREA) | 0.1.1 (INTER_LINEAR) |
|---|---|---|
| 720p · 60 fps · 4000 kbps | 226% (entregava só ~50 fps) | **161%** (entrega 58 fps) |
| 720p · 30 fps · 2500 kbps | 123% | **85%** |
| 1080p · 30 fps · 4500 kbps | 183% | 171% (sem redução, quase igual) |
| 480p · 30 fps · 1200 kbps | 120% | ~70% |

A janela do app mostra o uso real ("CPU do app: N%", em % da CPU inteira) enquanto transmite.
**Regra prática:** 60 fps custa quase o dobro de 30 fps, e 1080p quase o dobro de 720p. Para jogo exigente,
720p a 30 fps é o mais leve que ainda fica bom.

Testado e descartado: baixar a prioridade do processo (`BELOW_NORMAL`). Com cargas simulando um jogo em 6, 8 e
12 threads, a diferença no ritmo do "jogo" ficou entre -8 e +5 pontos, ora a favor ora contra: o custo está em
dividir núcleos físicos (hyper-threading), não em disputa de escalonador. Reduzir trabalho é o que funciona.

Custo da captura (sem contar o encode), com o vídeo indo de fato para um `VideoSource` (medido na 0.1.0,
antes da troca da redução; hoje é ~30% menor nas linhas com redução):

| Saída | ms por quadro | CPU da thread de mídia |
|---|---|---|
| 1280x720 @ 30 | 13,8 | 60% de um núcleo |
| 1280x720 @ 60 | 13,2 | 121% |
| 1920x1080 @ 60 (nativo, sem redução) | 11,9 | 67% |

Transmitir na resolução nativa da tela sai mais barato que reduzir.

## Estrutura

| Caminho | Responsabilidade |
|---|---|
| `cliente/__main__.py` | Monta as threads, impede duas instâncias (mutex), log em arquivo |
| `cliente/conexao.py` | WebSocket: handshake, reconexão com backoff, códigos 1012/4001-4003 |
| `cliente/controlador.py` | `ack` imediato, iniciar/parar, parada local, queda da mídia, estado exibido |
| `cliente/media/captura.py` | Fontes DXGI/GDI/janela e o laço de captura na thread de mídia |
| `cliente/media/fontes.py` | Lista telas e janelas, miniaturas, reencontra a janela salva |
| `cliente/ui/transmissao.py` | Painel de fonte/qualidade e o seletor estilo Go Live |
| `cliente/media/escala.py` | Encaixe na resolução pedida (sem distorcer, sem aumentar) e redução |
| `cliente/media/cursor.py` | Cursor do Windows desenhado no quadro |
| `cliente/media/publicador.py` | Sala do LiveKit e publicação da track de tela |
| `cliente/ui/` | Janela, bandeja e ícones por estado |
| `cliente/protocolo.py` | Espelho do protocolo do backend (um teste garante que não divergem) |
