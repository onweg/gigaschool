"""Запись событий. Тонкий слой поверх db, чтобы вызовы в main.py читались."""
from __future__ import annotations

from typing import Any

from app.metrics import db


def session_created(
    *,
    session_id: str,
    device_id: str,
    user_agent: str,
    request_text: str,
    program_type: str,
    total_hours: float | None,
    audience: str,
    has_references: bool,
    questions_asked: int,
    provider: str,
    model: str,
) -> None:
    db.write(
        """INSERT OR IGNORE INTO sessions
           (id, created_at, device_id, user_agent, request_text, program_type,
            total_hours, audience, has_references, questions_asked, provider, model)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id, db.now(), device_id, user_agent[:400], request_text[:2000],
            program_type, total_hours, audience[:200], int(has_references),
            questions_asked, provider, model,
        ),
    )
    stage(session_id=session_id, device_id=device_id, name="created")


def stage(*, session_id: str, device_id: str = "", name: str, note: str = "") -> None:
    db.write(
        "INSERT INTO stage_events (ts, session_id, device_id, stage, note) VALUES (?,?,?,?,?)",
        (db.now(), session_id, device_id, name, note[:500]),
    )


def run(
    *,
    session_id: str,
    device_id: str = "",
    phase: str,
    started_at: str,
    duration_s: float,
    ok: bool,
    error: str = "",
    llm_calls: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_rub: float = 0.0,
    provider: str = "",
    model: str = "",
) -> None:
    db.write(
        """INSERT INTO runs
           (session_id, device_id, phase, started_at, finished_at, duration_s, ok,
            error, llm_calls, prompt_tokens, completion_tokens, cost_rub, provider, model)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id, device_id, phase, started_at, db.now(), round(duration_s, 3),
            int(ok), error[:500], llm_calls, prompt_tokens, completion_tokens,
            round(cost_rub, 4), provider, model,
        ),
    )


def llm_call(
    *,
    session_id: str = "",
    skill: str = "",
    provider: str = "",
    model: str = "",
    latency_ms: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    ok: bool = True,
    error: str = "",
    json_retries: int = 0,
    cost_rub: float = 0.0,
) -> None:
    db.write(
        """INSERT INTO llm_calls
           (ts, session_id, skill, provider, model, latency_ms, prompt_tokens,
            completion_tokens, ok, error, json_retries, cost_rub)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            db.now(), session_id, skill, provider, model, latency_ms,
            prompt_tokens, completion_tokens, int(ok), error[:500], json_retries, cost_rub,
        ),
    )


def findings(*, session_id: str, check_stage: str, report: Any) -> None:
    rows = getattr(report, "findings", None) or []
    for f in rows:
        db.write(
            """INSERT INTO findings (ts, session_id, check_stage, code, severity, message, path)
               VALUES (?,?,?,?,?,?,?)""",
            (
                db.now(), session_id, check_stage, getattr(f, "code", ""),
                getattr(f, "severity", ""), str(getattr(f, "message", ""))[:500],
                str(getattr(f, "path", ""))[:200],
            ),
        )


def export(*, session_id: str, device_id: str = "", kind: str, size: int) -> None:
    db.write(
        "INSERT INTO exports (ts, session_id, device_id, kind, bytes) VALUES (?,?,?,?,?)",
        (db.now(), session_id, device_id, kind, size),
    )


def action(*, session_id: str = "", device_id: str = "", kind: str, **payload: Any) -> None:
    db.write(
        "INSERT INTO actions (ts, session_id, device_id, kind, payload) VALUES (?,?,?,?,?)",
        (db.now(), session_id, device_id, kind, db.dumps(payload)),
    )
