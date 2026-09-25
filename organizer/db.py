"""Histórico local (SQLite): tudo o que passou pela pasta monitorada."""
import json
import sqlite3
import threading
from datetime import datetime

from .config import DB_PATH

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row

_conn.executescript("""
CREATE TABLE IF NOT EXISTS eventos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    quando      TEXT NOT NULL,
    evento      TEXT NOT NULL,      -- novo, organizado, nao_reconhecido, apagado, movido, erro
    arquivo     TEXT NOT NULL,      -- nome original do arquivo
    caminho     TEXT,               -- caminho de origem
    destino     TEXT,               -- caminho final (se foi movido/renomeado)
    tipo        TEXT,               -- nome do tipo de documento identificado
    campos      TEXT,               -- JSON com os dados extraídos
    resumo      TEXT,               -- descrição curta feita pela IA
    observacao  TEXT,               -- erros de validação, avisos
    hash        TEXT
);
CREATE INDEX IF NOT EXISTS idx_eventos_quando ON eventos(quando);

-- Arquivos que o programa sabe que existem hoje (para detectar exclusões).
CREATE TABLE IF NOT EXISTS arquivos (
    caminho  TEXT PRIMARY KEY,
    hash     TEXT,
    tamanho  INTEGER,
    visto_em TEXT
);

-- Resultado da IA por conteúdo, para não pagar duas vezes pelo mesmo arquivo.
CREATE TABLE IF NOT EXISTS cache_ia (
    hash      TEXT PRIMARY KEY,
    resultado TEXT NOT NULL
);
""")
_conn.commit()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_event(evento: str, arquivo: str, caminho: str | None = None, destino: str | None = None,
              tipo: str | None = None, campos: dict | None = None, resumo: str | None = None,
              observacao: str | None = None, hash_: str | None = None) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO eventos (quando, evento, arquivo, caminho, destino, tipo, campos, resumo, observacao, hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), evento, arquivo, caminho, destino, tipo,
             json.dumps(campos, ensure_ascii=False) if campos else None, resumo, observacao, hash_),
        )
        _conn.commit()


def search_events(texto: str = "", evento: str = "", limite: int = 500) -> list[sqlite3.Row]:
    sql = "SELECT * FROM eventos WHERE 1=1"
    params: list = []
    if texto:
        sql += " AND (arquivo LIKE ? OR destino LIKE ? OR tipo LIKE ? OR campos LIKE ? OR resumo LIKE ? OR observacao LIKE ?)"
        params += [f"%{texto}%"] * 6
    if evento:
        sql += " AND evento = ?"
        params.append(evento)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limite)
    with _lock:
        return _conn.execute(sql, params).fetchall()


# ---- arquivos conhecidos ----

def track(caminho: str, hash_: str | None, tamanho: int) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO arquivos (caminho, hash, tamanho, visto_em) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(caminho) DO UPDATE SET hash=excluded.hash, tamanho=excluded.tamanho, visto_em=excluded.visto_em",
            (caminho, hash_, tamanho, _now()),
        )
        _conn.commit()


def untrack(caminho: str) -> None:
    with _lock:
        _conn.execute("DELETE FROM arquivos WHERE caminho = ?", (caminho,))
        _conn.commit()


def retrack(antigo: str, novo: str) -> None:
    with _lock:
        _conn.execute("UPDATE OR REPLACE arquivos SET caminho = ? WHERE caminho = ?", (novo, antigo))
        _conn.commit()


def tracked_paths() -> set[str]:
    with _lock:
        return {r["caminho"] for r in _conn.execute("SELECT caminho FROM arquivos")}


def is_tracked(caminho: str) -> bool:
    with _lock:
        return _conn.execute("SELECT 1 FROM arquivos WHERE caminho = ?", (caminho,)).fetchone() is not None


# ---- cache da IA ----

def cache_get(hash_: str) -> dict | None:
    with _lock:
        row = _conn.execute("SELECT resultado FROM cache_ia WHERE hash = ?", (hash_,)).fetchone()
    return json.loads(row["resultado"]) if row else None


def cache_put(hash_: str, resultado: dict) -> None:
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO cache_ia (hash, resultado) VALUES (?, ?)",
                      (hash_, json.dumps(resultado, ensure_ascii=False)))
        _conn.commit()
