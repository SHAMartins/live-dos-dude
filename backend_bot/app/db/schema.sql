-- Base (v1), idempotente: roda a cada inicialização. Mudanças posteriores vão em MIGRACOES (repositorio.py).

CREATE TABLE IF NOT EXISTS usuarios (
    discord_id  INTEGER PRIMARY KEY,
    nome        TEXT NOT NULL,
    criado_em   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Um PC por usuário. Só o hash do token fica salvo: vazar o banco não dá acesso aos PCs.
CREATE TABLE IF NOT EXISTS dispositivos (
    id          INTEGER PRIMARY KEY,
    discord_id  INTEGER NOT NULL UNIQUE REFERENCES usuarios (discord_id) ON DELETE CASCADE,
    nome        TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    pareado_em  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    visto_em    TEXT
);

CREATE TABLE IF NOT EXISTS codigos_pareamento (
    codigo      TEXT PRIMARY KEY,
    discord_id  INTEGER NOT NULL REFERENCES usuarios (discord_id) ON DELETE CASCADE,
    expira_em   REAL NOT NULL  -- epoch em segundos
);

-- Padrões de cada sala; os parâmetros do /transmitir sobrescrevem por sessão.
CREATE TABLE IF NOT EXISTS salas (
    nome          TEXT PRIMARY KEY,
    resolucao     TEXT NOT NULL DEFAULT '720p',
    fps           INTEGER NOT NULL DEFAULT 30,
    bitrate_kbps  INTEGER NOT NULL DEFAULT 2500,
    codec         TEXT NOT NULL DEFAULT 'vp8',
    criada_por    INTEGER REFERENCES usuarios (discord_id) ON DELETE SET NULL,
    criada_em     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessoes (
    id            TEXT PRIMARY KEY,  -- = id da mensagem 'iniciar'
    discord_id    INTEGER NOT NULL REFERENCES usuarios (discord_id) ON DELETE CASCADE,
    sala          TEXT NOT NULL REFERENCES salas (nome),
    resolucao     TEXT NOT NULL,
    fps           INTEGER NOT NULL,
    bitrate_kbps  INTEGER NOT NULL,
    codec         TEXT NOT NULL,
    estado        TEXT NOT NULL CHECK (estado IN ('solicitada', 'ativa', 'falhou', 'encerrada')),
    detalhe       TEXT NOT NULL DEFAULT '',
    iniciada_em   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    encerrada_em  TEXT
);

CREATE INDEX IF NOT EXISTS sessoes_por_usuario ON sessoes (discord_id, estado);

