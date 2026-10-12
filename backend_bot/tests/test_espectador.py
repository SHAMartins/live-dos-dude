"""Link com chave: página, emissão do token de espectador e expiração junto com a transmissão."""

from __future__ import annotations

import sqlite3

from livekit import api

from app.db.repositorio import Repositorio
from app.services.transmissao import PedidoTransmissao
from auxiliares import CICLANO, FULANO


def _verificar(ambiente, token: str):
    return api.TokenVerifier(ambiente.config.livekit_api_key,
                             ambiente.config.livekit_api_secret.get_secret_value()).verify(token)


async def _transmitindo(ambiente, cliente_pareado, usuario=FULANO, sala="dev"):
    await cliente_pareado(usuario)
    return await ambiente.servico.transmitir(usuario, PedidoTransmissao(sala))


async def test_cada_transmissao_ganha_um_link_com_chave_imprevisivel(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    assert len(sessao.chave_espectador) >= 22  # 128 bits em base64url
    assert ambiente.servico.link_espectador(sessao) == f"http://127.0.0.1:8000/assistir/{sessao.chave_espectador}"


async def test_pagina_mostra_quem_transmite_mas_nao_carrega_token(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    resposta = await ambiente.app.test_client().get(f"/assistir/{sessao.chave_espectador}")
    html = await resposta.get_data(as_text=True)

    assert resposta.status_code == 200
    assert 'property="og:title" content="🔴 Fulano ao vivo na sala dev"' in html
    assert "eyJ" not in html  # nenhum JWT: o preview do Discord não ganha acesso ao vídeo
    assert 'integrity="sha384-' in html


async def test_pagina_tem_cabecalhos_de_seguranca(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    resposta = await ambiente.app.test_client().get(f"/assistir/{sessao.chave_espectador}")
    csp = resposta.headers["Content-Security-Policy"]

    assert resposta.headers["Referrer-Policy"] == "no-referrer"  # a chave está na URL
    assert "connect-src 'self' ws://localhost:7880 http://localhost:7880" in csp
    assert "script-src 'self' https://cdn.jsdelivr.net" in csp
    assert "frame-ancestors 'none'" in csp
    assert resposta.headers["X-Robots-Tag"] == "noindex, nofollow"


async def test_arquivos_estaticos_sao_servidos(ambiente):
    cliente = ambiente.app.test_client()
    for caminho in ("/static/assistir.js", "/static/assistir.css"):
        resposta = await cliente.get(caminho)
        assert resposta.status_code == 200, caminho
        assert resposta.headers["Referrer-Policy"] == "no-referrer"


async def test_pagina_aponta_para_a_versao_atual_dos_estaticos(ambiente):
    # Os estáticos ficam 12 h em cache: o endereço precisa mudar quando o conteúdo muda.
    import hashlib
    import re
    from pathlib import Path

    cliente = ambiente.app.test_client()
    resposta = await cliente.get("/assistir/qualquer")
    html = await resposta.get_data(as_text=True)
    assert resposta.headers["Cache-Control"] == "no-cache"
    pasta = Path(ambiente.app.static_folder)
    for nome in ("assistir.js", "assistir.css"):
        versao = re.search(rf'/static/{re.escape(nome)}\?v=([0-9a-f]+)"', html).group(1)
        assert hashlib.sha256((pasta / nome).read_bytes()).hexdigest().startswith(versao)
        assert (await cliente.get(f"/static/{nome}?v={versao}")).status_code == 200


async def test_chave_valida_da_token_so_de_assistir_e_so_nesta_sala(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    resposta = await ambiente.app.test_client().post(f"/api/assistir/{sessao.chave_espectador}")
    dados = await resposta.get_json()

    assert resposta.status_code == 200
    assert resposta.headers["Cache-Control"] == "no-store"
    assert (dados["sala"], dados["transmissor"], dados["livekit_url"]) == ("dev", "Fulano", "ws://localhost:7880")
    claims = _verificar(ambiente, dados["token"])
    assert claims.video.room == "dev"
    assert claims.video.can_subscribe and not claims.video.can_publish and not claims.video.can_publish_data
    assert claims.identity.startswith("espectador-")


async def test_dois_espectadores_recebem_identidades_diferentes(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    cliente = ambiente.app.test_client()
    tokens = [(await (await cliente.post(f"/api/assistir/{sessao.chave_espectador}")).get_json())["token"]
              for _ in range(2)]
    assert _verificar(ambiente, tokens[0]).identity != _verificar(ambiente, tokens[1]).identity


async def test_chave_errada_ou_malformada_da_404(ambiente, cliente_pareado):
    await _transmitindo(ambiente, cliente_pareado)
    cliente = ambiente.app.test_client()
    for chave in ("a" * 22, "curta", "com%20espaco", "x" * 200):
        assert (await cliente.post(f"/api/assistir/{chave}")).status_code == 404, chave
    pagina = await cliente.get("/assistir/" + "a" * 22)
    assert pagina.status_code == 200  # a página abre e explica que o link expirou
    assert 'content="Live dos Dude"' in await pagina.get_data(as_text=True)


async def test_link_morre_quando_a_transmissao_acaba(ambiente, cliente_pareado):
    sessao = await _transmitindo(ambiente, cliente_pareado)
    await ambiente.servico.parar(FULANO)
    resposta = await ambiente.app.test_client().post(f"/api/assistir/{sessao.chave_espectador}")
    assert resposta.status_code == 404


async def test_assistir_lista_os_links_ativos_da_sala(ambiente, cliente_pareado):
    a = await _transmitindo(ambiente, cliente_pareado, FULANO, "dev")
    b = await _transmitindo(ambiente, cliente_pareado, CICLANO, "dev")
    links = await ambiente.servico.links_da_sala("dev")
    assert links == [("Fulano", ambiente.servico.link_espectador(a)), ("Ciclano", ambiente.servico.link_espectador(b))]
    assert await ambiente.servico.links_da_sala("vazia") == []


async def test_banco_da_versao_anterior_ganha_a_coluna_sem_perder_dados(tmp_path):
    caminho = tmp_path / "antigo.sqlite3"
    antigo = sqlite3.connect(caminho)  # schema da v1, como estava no banco de dev
    antigo.executescript("""
        CREATE TABLE usuarios (discord_id INTEGER PRIMARY KEY, nome TEXT NOT NULL, criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE sessoes (id TEXT PRIMARY KEY, discord_id INTEGER NOT NULL, sala TEXT NOT NULL, resolucao TEXT NOT NULL,
            fps INTEGER NOT NULL, bitrate_kbps INTEGER NOT NULL, codec TEXT NOT NULL, estado TEXT NOT NULL,
            detalhe TEXT NOT NULL DEFAULT '', iniciada_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, encerrada_em TEXT);
        INSERT INTO usuarios (discord_id, nome) VALUES (1, 'Antigo');
        INSERT INTO sessoes (id, discord_id, sala, resolucao, fps, bitrate_kbps, codec, estado)
            VALUES ('s1', 1, 'dev', '720p', 30, 2500, 'vp8', 'encerrada');
        PRAGMA user_version = 1;
    """)
    antigo.close()

    repo = await Repositorio.abrir(caminho)
    sessao = await repo.sessao("s1")
    await repo.fechar()
    assert sessao.sala == "dev" and sessao.chave_espectador is None
    with sqlite3.connect(caminho) as conexao:
        assert conexao.execute("PRAGMA user_version").fetchone()[0] == 2

    reaberto = await Repositorio.abrir(caminho)  # abrir de novo não reaplica a migração
    await reaberto.fechar()


def test_csp_libera_os_servidores_de_borda_do_livekit_cloud():
    from app.api.espectador import politica_de_conteudo

    csp = politica_de_conteudo("wss://live-dos-dude-abc123.livekit.cloud")
    assert "connect-src 'self' wss://*.livekit.cloud https://*.livekit.cloud" in csp
    proprio = politica_de_conteudo("wss://livedosdude-live.duckdns.org")
    assert "connect-src 'self' wss://livedosdude-live.duckdns.org https://livedosdude-live.duckdns.org" in proprio
    assert "*" not in proprio.split("connect-src")[1].split(";")[0]  # servidor próprio: nada de curinga
