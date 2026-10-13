# Investigação: codificar o vídeo na GPU

Data: 2026-10-12. Máquina de teste: i5-10400F (6 núcleos / 12 threads), RTX 2060 Super, driver 581.80,
Windows 10. Conteúdo: tela em movimento constante, 720p a 60 fps, ~4000 kbps, um espectador.
Todos os valores de CPU são do pipeline inteiro, em % de **um** núcleo lógico.

Contexto: a restrição original do projeto é "codificação só na CPU". Isto avalia a GPU como **opção ligada
pelo usuário**, não como padrão.

## Resultado em uma frase

O encoder de hardware existe e funciona nesta máquina, mas **o caminho do SDK atual não o alcança**, e
usá-lo por fora do SDK reduz a CPU em cerca de 40%, não em 90%: o resto do gasto é captura, download da
imagem e redução, que continuam na CPU.

## 1. O SDK do LiveKit (`livekit` 1.1.20, Windows)

| Verificação | Resultado |
|---|---|
| `video_encoder = NVENC / HARDWARE / AUTO` | Aceita, mas o stats mostra `encoder_implementation = OpenH264`, `power_efficient_encoder = False`: **caiu em software sem erro** |
| Por quê | A DLL nativa contém a mensagem *"LIVEKIT_PREFERRED_HW_ENCODER=nvenc requested, but NVENC support is not compiled in"*: o binário publicado para Windows foi compilado sem NVENC (nem VAAPI) |
| Modo `PRE_ENCODED` | **Existe e funciona.** O FFI expõe `NewVideoSource(type=VIDEO_SOURCE_ENCODED)`, `CaptureEncodedVideoFrame` e `TakeEncodedVideoSourceFeedback` (pedido de keyframe + taxa-alvo). A API Python não envolve isso, mas dá para chamar o FFI direto (protótipo abaixo) |

## 2. Protótipo do pass-through

[cliente_desktop/experimentos/pass_through_codificado.py](../cliente_desktop/experimentos/pass_through_codificado.py):
ffmpeg captura (`ddagrab`) e codifica; o Python separa as unidades de acesso H.264 (Annex-B) e entrega ao
LiveKit pelo FFI. Um espectador decodificou 1280x720 normalmente.

| Pipeline (720p60) | CPU total | Encoder da GPU |
|---|---|---|
| App atual (VP8 por software, 0.1.1) | ~120-160% | – |
| ffmpeg + x264 `ultrafast` + pass-through | 108% (ffmpeg 92 + Python 16) | 0% |
| ffmpeg + **`h264_mf`** (Media Foundation) + pass-through | **90%** (ffmpeg 77 + Python 13) | 5-9% (nvidia-smi) |

- O `h264_mf` usou o NVENC da placa. Ele é **neutro de fabricante** (o Windows escolhe o encoder da
  NVIDIA, AMD ou Intel), então uma opção "usar a GPU" pelo Media Foundation não ficaria presa à NVIDIA.
- Os 77% do ffmpeg são captura + cópia do quadro 1080p BGRA da GPU para a RAM + redução na CPU. Codificar
  na GPU não toca nessa parte.

## 3. O que não funcionou (e por quê)

| Tentativa | Resultado |
|---|---|
| `h264_nvenc` no ffmpeg 9.0.2 | Recusa: *"Driver does not support the required nvenc API version. Required: 13.1 Found: 13.0"*. Precisa de driver ≥ 610 (ou um ffmpeg mais antigo) |
| Redução na GPU (`scale_d3d11`, nv12/bgra) | *"Could not create the texture (80070057)"* ao configurar o filtro |
| `hwmap` para D3D12 | *"Failed to created derived device context"* |

Com a redução na GPU funcionando e o NVENC direto (sem baixar a imagem), o custo de CPU poderia cair para
bem menos de 50%. **Não consegui medir isso** nesta máquina/driver.

## 4. Limitações do pass-through com ffmpeg

- **Keyframe sob demanda:** o SDK avisa (`keyframe_requested = True`) quando um espectador entra ou há perda,
  mas o ffmpeg em linha de comando não aceita esse pedido em tempo de execução. Só dá para usar GOP fixo
  (ex.: 1 s), pagando ~10-15% de bitrate. Um espectador novo espera até 1 GOP pela imagem.
- **Taxa adaptativa:** o `rate_control` do feedback (taxa-alvo) também não é aplicável ao ffmpeg em
  execução; cairia o ajuste automático de qualidade com a rede.
- **Captura de janela:** o `ddagrab` captura uma saída (monitor). A captura de janela por WGC, que o app
  usa hoje, teria que ser refeita (ou ficaria só a tela inteira).
- **Troca de fonte/qualidade ao vivo ("Aplicar agora")** passa a exigir reiniciar o processo do ffmpeg.
- **Distribuição:** o app teria que embutir o ffmpeg (dezenas de MB) com as licenças dele.
- O som continua pelo caminho atual do SDK; não é afetado.

## 5. Caminhos possíveis, do mais barato ao mais caro

1. **Ficar como está** (0.1.1): 720p a 30 fps custa ~85%. A queda de 60 para 30 fps (-47%) rende quase o
   mesmo que a GPU (-30 a -45%) sem nenhuma complexidade nova.
2. **Modo "GPU (experimental)" por ffmpeg + `h264_mf` + pass-through**: ganho de ~40% em 720p60, com todas as
   limitações da seção 4. Estimativa: vários dias de trabalho, mais testes em placas AMD/Intel.
3. **Encoder dentro do processo** (NVENC pelo SDK da NVIDIA, ou Media Foundation direto): permite keyframe
   sob demanda e taxa dinâmica, e junto com redução na GPU é o que chega mais perto de CPU quase nula. É o
   mais trabalhoso e só cobre quem tem a placa certa.

Recomendação: **não fazer agora.** Testar a 0.1.1 em jogo e, só se ainda pesar em 720p/30, abrir o item 2 ou 3.
