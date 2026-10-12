"""Página do espectador: GET /assistir/<chave> (HTML) e POST /api/assistir/<chave> (token).

O HTML não carrega token nenhum: o preview de link do Discord (e qualquer robô que siga o
link) só vê o título. O token só é emitido quando o JavaScript da página pede, por POST.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from quart import Quart, Response, jsonify, render_template, request

from app.services.transmissao import ServicoTransmissao

LIVEKIT_JS = "https://cdn.jsdelivr.net/npm/livekit-client@2.22.3/dist/livekit-client.umd.js"
# SRI: se o CDN entregar um arquivo diferente deste, o navegador se recusa a rodá-lo.
LIVEKIT_JS_SRI = "sha384-G/xxtkVytOx/ia9Q8MXxM+V0ohsaY1fZAgVP3iSGTPz4wJ0s3+ulJNKXh/gnzEDZ"


def politica_de_conteudo(livekit_url: str) -> str:
    """CSP: só os scripts do próprio backend e do CDN fixado; conexões só com o backend e o LiveKit."""
    partes = urlsplit(livekit_url)
    seguro = partes.scheme == "wss"
    host = partes.netloc
    if (partes.hostname or "").endswith(".livekit.cloud"):
        # No LiveKit Cloud o SDK consulta as regiões e conecta num servidor de borda com
        # outro nome (projeto.<região>.production.livekit.cloud): precisa do curinga.
        host = "*.livekit.cloud"
    livekit_ws = f"{partes.scheme}://{host}"
    livekit_http = f"{'https' if seguro else 'http'}://{host}"  # o SDK valida o token via HTTP
    return "; ".join([
        "default-src 'none'",
        f"script-src 'self' {LIVEKIT_JS.split('/npm/')[0]}",
        "style-src 'self'",
        "img-src 'self' data:",
        "media-src 'self' blob:",
        f"connect-src 'self' {livekit_ws} {livekit_http}",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ])


def endereco_versionado(pasta_estatica: Path, nome: str) -> str:
    """/static/<nome>?v=<hash do conteúdo>: cada deploy que muda o arquivo muda o endereço.

    Os estáticos vão com cache de 12 h; sem isto, quem abriu a página antes de um deploy continua
    rodando o JavaScript velho (foi assim que o som "não chegou" logo depois de ser lançado).
    """
    versao = hashlib.sha256((pasta_estatica / nome).read_bytes()).hexdigest()[:12]
    return f"/static/{nome}?v={versao}"


def registrar_espectador(app: Quart, servico: ServicoTransmissao) -> None:
    csp = politica_de_conteudo(servico.config.livekit_url)
    pasta_estatica = Path(app.static_folder)
    js, css = (endereco_versionado(pasta_estatica, nome) for nome in ("assistir.js", "assistir.css"))

    @app.get("/assistir/<chave>")
    async def pagina(chave: str) -> Response:
        descricao = await servico.descrever_link(chave)
        if descricao:
            sala, transmissor = descricao
            titulo = f"🔴 {transmissor} ao vivo na sala {sala}"
            resumo = "Clique para assistir no navegador. Não precisa instalar nada."
        else:
            titulo, resumo = "Live dos Dude", "Transmissão encerrada ou link inválido."
        html = await render_template("assistir.html", titulo=titulo, resumo=resumo,
                                     livekit_js=LIVEKIT_JS, livekit_js_sri=LIVEKIT_JS_SRI,
                                     assistir_js=js, assistir_css=css)
        return Response(html, content_type="text/html; charset=utf-8")

    @app.post("/api/assistir/<chave>")
    async def acesso(chave: str) -> tuple[Response, int] | Response:
        acesso = await servico.acesso_por_chave(chave)
        if acesso is None:
            return jsonify(erro="Transmissão encerrada ou link inválido."), 404
        return jsonify(livekit_url=acesso.livekit_url, token=acesso.token,
                       sala=acesso.sala, transmissor=acesso.transmissor)

    @app.after_request
    async def cabecalhos(resposta: Response) -> Response:
        caminho = request.path
        if caminho.startswith(("/assistir/", "/static/")):
            resposta.headers["Content-Security-Policy"] = csp
            # A chave está no caminho da URL: sem isto ela vazaria no Referer para o CDN.
            resposta.headers["Referrer-Policy"] = "no-referrer"
            resposta.headers["X-Content-Type-Options"] = "nosniff"
            resposta.headers["X-Robots-Tag"] = "noindex, nofollow"
        if caminho.startswith("/assistir/"):
            resposta.headers["Cache-Control"] = "no-cache"  # o HTML aponta para as versões atuais
        if caminho.startswith("/api/"):
            resposta.headers["Cache-Control"] = "no-store"
        return resposta
