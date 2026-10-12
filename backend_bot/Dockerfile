# Backend (bot + API). Funciona em x86 e ARM (a imagem base tem as duas arquiteturas).
FROM python:3.13.16-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# Usuário sem privilégios; o volume de dados herda o dono desta pasta na primeira vez.
RUN useradd --system --uid 10001 --home-dir /srv backend \
    && mkdir -p /srv/dados \
    && chown backend /srv/dados
USER backend

ENV BANCO=/srv/dados/backend.sqlite3 \
    HOST=127.0.0.1 \
    PORTA=8000
VOLUME ["/srv/dados"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORTA', '8000') + '/saude', timeout=2)"]

CMD ["python", "-m", "app"]
