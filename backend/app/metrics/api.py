"""Запросы для дашборда. Только чтение, ничего не удаляется."""
from __future__ import annotations

from typing import Any

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.config import get_settings
from app.metrics import db


def require_token(request: Request) -> None:
    """Телеметрия закрыта токеном.

    В ней тексты запросов методистов, модели, стоимость генераций — это не то,
    что отдают по ссылке. Токен принимается заголовком (так ходит интерфейс)
    или параметром запроса (так удобно открыть страницу и выгрузить CSV).
    Пока токен не задан, раздел закрыт целиком: «открыто по умолчанию» —
    это не настройка, а недосмотр.
    """
    expected = get_settings().metrics_token
    if not expected:
        raise HTTPException(
            503,
            "статистика закрыта: задайте METRICS_TOKEN в окружении сервиса",
        )
    supplied = request.headers.get("x-metrics-token") or request.query_params.get("token")
    if supplied != expected:
        raise HTTPException(401, "нужен токен доступа к статистике")


router = APIRouter(
    prefix="/api/metrics", tags=["metrics"], dependencies=[Depends(require_token)]
)

#: Стадии воронки в порядке прохождения.
FUNNEL = ["created", "clarifying", "skeleton", "approved", "content", "ready", "failed"]


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _percentiles(values: list[float]) -> dict[str, float]:
    """p50/p90 на отсортированном списке — выборки маленькие, точность не нужна."""
    if not values:
        return {"p50": 0.0, "p90": 0.0, "max": 0.0}
    ordered = sorted(values)

    def at(q: float) -> float:
        idx = min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))
        return round(ordered[idx], 2)

    return {"p50": at(0.5), "p90": at(0.9), "max": round(ordered[-1], 2)}


def _cost_sql():
    settings = get_settings()
    return f"COALESCE(cost_rub, prompt_tokens * {settings.price_prompt_per_1k / 1000} + completion_tokens * {settings.price_completion_per_1k / 1000})"


@router.get("/overview")
async def overview(days: int | None = None) -> dict[str, Any]:
    """Сводка: воронка, время фаз, качество, деньги. days=None — за всё время."""
    where = f"WHERE ts >= datetime('now', '-{int(days)} days')" if days else ""
    s_where = f"WHERE created_at >= datetime('now', '-{int(days)} days')" if days else ""

    totals = db.one(
        f"""SELECT COUNT(*) AS sessions,
                   COUNT(DISTINCT device_id) AS devices,
                   MIN(created_at) AS first_seen
            FROM sessions {s_where}"""
    )
    reached = {
        row["stage"]: row["n"]
        for row in db.query(
            f"""SELECT stage, COUNT(DISTINCT session_id) AS n
                FROM stage_events {where} GROUP BY stage"""
        )
    }
    started = totals.get("sessions") or 0
    funnel = [
        {
            "stage": name,
            "sessions": reached.get(name, 0),
            "share": _pct(reached.get(name, 0), started),
        }
        for name in FUNNEL
    ]

    phases = {}
    for phase in ("skeleton", "content", "regenerate"):
        rows = db.query(
            f"""SELECT duration_s, ok FROM runs
                WHERE phase = ? {'AND finished_at >= datetime("now", "-%d days")' % int(days) if days else ''}""",
            (phase,),
        )
        durations = [r["duration_s"] for r in rows if r["ok"]]
        phases[phase] = {
            "runs": len(rows),
            "failed": sum(1 for r in rows if not r["ok"]),
            **_percentiles(durations),
        }

    money = db.one(
        f"""SELECT COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                   COALESCE(SUM({_cost_sql()}),0) AS cost_rub,
                   COUNT(*) AS llm_calls
            FROM llm_calls {f"WHERE ts >= datetime('now', '-{int(days)} days')" if days else ""}"""
    )
    calls = db.one(
        f"""SELECT COUNT(*) AS n,
                   COALESCE(AVG(latency_ms),0) AS avg_latency_ms,
                   COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END),0) AS failed,
                   COALESCE(SUM(json_retries),0) AS json_retries
            FROM llm_calls {where}"""
    )

    # Качество: доля сессий, где полная автопроверка прошла без ошибок.
    checked = db.one(
        f"""SELECT COUNT(DISTINCT session_id) AS n FROM findings
            WHERE check_stage = 'full' {('AND ts >= datetime("now", "-%d days")' % int(days)) if days else ''}"""
    )
    dirty = db.one(
        f"""SELECT COUNT(DISTINCT session_id) AS n FROM findings
            WHERE check_stage = 'full' AND severity = 'error'
            {('AND ts >= datetime("now", "-%d days")' % int(days)) if days else ''}"""
    )
    checked_n = checked.get("n") or 0
    clean_n = max(0, checked_n - (dirty.get("n") or 0))

    exports = db.query(
        f"""SELECT kind, COUNT(*) AS n, COALESCE(SUM(bytes),0) AS bytes
            FROM exports {where} GROUP BY kind ORDER BY n DESC"""
    )

    return {
        "window_days": days,
        "sessions": started,
        "devices": totals.get("devices") or 0,
        "first_seen": totals.get("first_seen"),
        "funnel": funnel,
        "conversion_to_ready": _pct(reached.get("ready", 0), started),
        "phases": phases,
        "llm": {
            "calls": calls.get("n") or 0,
            "failed": calls.get("failed") or 0,
            "json_retries": calls.get("json_retries") or 0,
            "avg_latency_ms": round(calls.get("avg_latency_ms") or 0),
            "prompt_tokens": money.get("prompt_tokens") or 0,
            "completion_tokens": money.get("completion_tokens") or 0,
            "cost_rub": round(money.get("cost_rub") or 0, 2),
        },
        "quality": {
            "checked_sessions": checked_n,
            "clean_sessions": clean_n,
            "clean_share": _pct(clean_n, checked_n),
        },
        "exports": exports,
    }


@router.get("/timeseries")
async def timeseries(days: int = 30) -> dict[str, Any]:
    """По дням: сессии, готовые комплекты, стоимость."""
    rows = db.query(
        """SELECT date(created_at) AS day, COUNT(*) AS sessions
           FROM sessions WHERE created_at >= datetime('now', ?)
           GROUP BY day ORDER BY day""",
        (f"-{int(days)} days",),
    )
    ready = {
        r["day"]: r["n"]
        for r in db.query(
            """SELECT date(ts) AS day, COUNT(DISTINCT session_id) AS n
               FROM stage_events WHERE stage='ready' AND ts >= datetime('now', ?)
               GROUP BY day""",
            (f"-{int(days)} days",),
        )
    }
    cost = {
        r["day"]: r["cost"]
        for r in db.query(
            f"""SELECT date(ts) AS day, SUM({_cost_sql()}) AS cost
               FROM llm_calls WHERE ts >= datetime('now', ?) GROUP BY day""",
            (f"-{int(days)} days",),
        )
    }
    return {
        "days": [
            {
                "day": r["day"],
                "sessions": r["sessions"],
                "ready": ready.get(r["day"], 0),
                "cost_rub": round(cost.get(r["day"], 0) or 0, 2),
            }
            for r in rows
        ]
    }


@router.get("/findings")
async def top_findings(limit: int = 15) -> dict[str, Any]:
    """На чём агент спотыкается чаще всего — это очередь задач на доработку."""
    return {
        "codes": db.query(
            """SELECT code, severity, COUNT(*) AS n,
                      COUNT(DISTINCT session_id) AS sessions,
                      MAX(message) AS example
               FROM findings GROUP BY code, severity
               ORDER BY n DESC LIMIT ?""",
            (limit,),
        )
    }


@router.get("/skills")
async def by_skill() -> dict[str, Any]:
    """Разрез по навыкам: где дорого, где медленно, где модель отказывает."""
    return {
        "skills": db.query(
            """SELECT skill,
                      COUNT(*) AS calls,
                      ROUND(AVG(latency_ms)) AS avg_latency_ms,
                      MAX(latency_ms) AS max_latency_ms,
                      SUM(prompt_tokens) AS prompt_tokens,
                      SUM(completion_tokens) AS completion_tokens,
                      SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failed,
                      SUM(json_retries) AS json_retries
               FROM llm_calls GROUP BY skill ORDER BY calls DESC"""
        )
    }


@router.get("/sessions")
async def recent_sessions(limit: int = 50) -> dict[str, Any]:
    """Журнал сессий для отладки: что просили, чем кончилось, сколько стоило."""
    return {
        "sessions": db.query(
            f"""SELECT s.id, s.created_at, s.device_id, s.program_type, s.total_hours,
                      s.questions_asked, s.has_references, s.provider, s.model,
                      s.request_text,
                      (SELECT stage FROM stage_events e WHERE e.session_id = s.id
                        ORDER BY e.id DESC LIMIT 1) AS last_stage,
                      (SELECT COUNT(*) FROM llm_calls c WHERE c.session_id = s.id) AS calls,
                      (SELECT ROUND(SUM(duration_s),1) FROM runs r WHERE r.session_id = s.id) AS seconds,
                      (SELECT ROUND(SUM({_cost_sql()}),2) FROM llm_calls c WHERE c.session_id = s.id) AS cost_rub,
                      (SELECT COUNT(*) FROM findings f
                        WHERE f.session_id = s.id AND f.severity='error') AS errors,
                      (SELECT COUNT(*) FROM exports x WHERE x.session_id = s.id) AS exports
               FROM sessions s ORDER BY s.created_at DESC LIMIT ?""",
            (limit,),
        )
    }


@router.get("/session/{session_id}")
async def session_detail(session_id: str) -> dict[str, Any]:
    """Полная карточка одной сессии — то, что нужно при разборе бага."""
    return {
        "session": db.one("SELECT * FROM sessions WHERE id = ?", (session_id,)),
        "stages": db.query(
            "SELECT ts, stage, note FROM stage_events WHERE session_id = ? ORDER BY id",
            (session_id,),
        ),
        "runs": db.query("SELECT * FROM runs WHERE session_id = ? ORDER BY id", (session_id,)),
        "calls": db.query(
            """SELECT ts, skill, model, latency_ms, prompt_tokens, completion_tokens, ok, error
               FROM llm_calls WHERE session_id = ? ORDER BY id""",
            (session_id,),
        ),
        "findings": db.query(
            "SELECT ts, check_stage, code, severity, message, path FROM findings WHERE session_id = ? ORDER BY id",
            (session_id,),
        ),
        "exports": db.query(
            "SELECT ts, kind, bytes FROM exports WHERE session_id = ? ORDER BY id", (session_id,)
        ),
        "actions": db.query(
            "SELECT ts, kind, payload FROM actions WHERE session_id = ? ORDER BY id", (session_id,)
        ),
    }


@router.get("/requests")
async def request_mix() -> dict[str, Any]:
    """Что вообще просят: типы программ и объёмы. Нужно, чтобы понимать,
    какие сценарии тестировать в первую очередь."""
    return {
        "program_types": db.query(
            """SELECT COALESCE(NULLIF(program_type,''),'не определён') AS program_type,
                      COUNT(*) AS n
               FROM sessions GROUP BY program_type ORDER BY n DESC"""
        ),
        "hours_buckets": db.query(
            """SELECT CASE
                        WHEN total_hours IS NULL THEN 'не указан'
                        WHEN total_hours < 36 THEN 'до 36'
                        WHEN total_hours < 72 THEN '36–71'
                        WHEN total_hours < 144 THEN '72–143'
                        WHEN total_hours < 256 THEN '144–255'
                        ELSE '256+'
                      END AS bucket,
                      COUNT(*) AS n
               FROM sessions GROUP BY bucket ORDER BY n DESC"""
        ),
        "actions": db.query(
            "SELECT kind, COUNT(*) AS n FROM actions GROUP BY kind ORDER BY n DESC"
        ),
    }


#: Выгружать можно только эти таблицы — имя подставляется в SQL.
TABLES = ("sessions", "stage_events", "runs", "llm_calls", "findings", "exports", "actions")


@router.get("/export.csv")
async def export_csv(table: str = "sessions") -> Response:
    """Сырые строки для анализа в Excel или pandas. Только чтение."""
    if table not in TABLES:
        raise HTTPException(400, f"доступные таблицы: {', '.join(TABLES)}")
    rows = db.query(f"SELECT * FROM {table} ORDER BY 1")  # noqa: S608 — имя из белого списка
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()) if rows else ["empty"])
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        # BOM — чтобы Excel открыл кириллицу без шаманства с кодировкой
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
    )
