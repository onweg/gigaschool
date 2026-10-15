"""Накопительное хранилище телеметрии.

Почему SQLite, а не файлы рядом с сессиями: метрики нужно не читать, а
*считать* — доли, перцентили, разрезы по дням и моделям. Это ровно то, для
чего нужен SQL. Файл один, лежит в DATA_DIR, переживает перезапуск контейнера
и копируется обычным cp. Схема без единого DELETE: данные только дописываются,
в том числе с разных устройств и за разные дни.

Переезд на Postgres = замена _connect и плейсхолдеров; запросы совместимые.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    device_id     TEXT NOT NULL DEFAULT '',
    user_agent    TEXT NOT NULL DEFAULT '',
    request_text  TEXT NOT NULL DEFAULT '',
    program_type  TEXT NOT NULL DEFAULT '',
    total_hours   REAL,
    audience      TEXT NOT NULL DEFAULT '',
    has_references INTEGER NOT NULL DEFAULT 0,
    questions_asked INTEGER NOT NULL DEFAULT 0,
    provider      TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT ''
);

-- Воронка: одна строка на достигнутую стадию, поэтому конверсия считается
-- как отношение количеств, а не как «последнее известное состояние».
CREATE TABLE IF NOT EXISTS stage_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    session_id TEXT NOT NULL,
    device_id  TEXT NOT NULL DEFAULT '',
    stage      TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    device_id     TEXT NOT NULL DEFAULT '',
    phase         TEXT NOT NULL,          -- skeleton | content | regenerate
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    duration_s    REAL NOT NULL,
    ok            INTEGER NOT NULL,
    error         TEXT NOT NULL DEFAULT '',
    llm_calls     INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_rub      REAL NOT NULL DEFAULT 0,
    provider      TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    session_id    TEXT NOT NULL DEFAULT '',
    skill         TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    ok            INTEGER NOT NULL DEFAULT 1,
    error         TEXT NOT NULL DEFAULT '',
    json_retries  INTEGER NOT NULL DEFAULT 0
);

-- Каждая находка автопроверки: отсюда видно, на чём агент спотыкается чаще.
CREATE TABLE IF NOT EXISTS findings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    session_id TEXT NOT NULL,
    check_stage TEXT NOT NULL DEFAULT '',
    code       TEXT NOT NULL,
    severity   TEXT NOT NULL,
    message    TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS exports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    session_id TEXT NOT NULL,
    device_id  TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL,
    bytes      INTEGER NOT NULL DEFAULT 0
);

-- Свободный журнал на всё остальное: правки часов, точечные доработки,
-- ошибки API. payload — JSON, чтобы не плодить колонки под каждый случай.
CREATE TABLE IF NOT EXISTS actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    device_id  TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_stage_session ON stage_events(session_id);
CREATE INDEX IF NOT EXISTS ix_stage_ts      ON stage_events(ts);
CREATE INDEX IF NOT EXISTS ix_runs_session  ON runs(session_id);
CREATE INDEX IF NOT EXISTS ix_runs_phase    ON runs(phase);
CREATE INDEX IF NOT EXISTS ix_calls_ts      ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS ix_calls_session ON llm_calls(session_id);
CREATE INDEX IF NOT EXISTS ix_find_code     ON findings(code);
CREATE INDEX IF NOT EXISTS ix_exports_kind  ON exports(kind);
CREATE INDEX IF NOT EXISTS ix_actions_kind  ON actions(kind);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def connect() -> sqlite3.Connection:
    """Одно соединение на процесс. WAL — чтобы фоновая генерация писала
    метрики, пока дашборд их читает, и никто никого не блокировал."""
    global _CONN
    if _CONN is not None:
        return _CONN
    with _LOCK:
        if _CONN is None:
            path = Path(get_settings().data_dir) / "metrics.sqlite3"
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            conn.commit()
            _CONN = conn
    return _CONN


def write(sql: str, params: tuple | dict = ()) -> None:
    """Запись метрики никогда не должна ронять основной сценарий."""
    try:
        conn = connect()
        with _LOCK:
            conn.execute(sql, params)
            conn.commit()
    except Exception:  # noqa: BLE001 — телеметрия не влияет на работу агента
        pass


def query(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    conn = connect()
    with _LOCK:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def one(sql: str, params: tuple | dict = ()) -> dict[str, Any]:
    rows = query(sql, params)
    return rows[0] if rows else {}


def dumps(payload: dict) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return "{}"
