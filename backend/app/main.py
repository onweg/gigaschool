"""HTTP API агента.

Единый эндпоинт решения (требование кейса): все шаги агента доступны через
REST, прогресс — через SSE, выгрузки — через /export. Логика шагов живёт
в app/pipeline, здесь только транспорт и управление стадиями сессии.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.config import build_provider, get_settings
from app.llm.metered import MeteredProvider, current_session
from app.metrics import db, record
from app.metrics.api import router as metrics_router
from app.core.hours import rebalance_topics, split_exact
from app.core.validate import validate_program
from app.pipeline.content import (
    build_content,
    fill_topic_content,
    generate_lectures,
    generate_seminars,
    generate_tests,
)
from app.pipeline.intake import apply_answers, reask_text, run_intake
from app.pipeline.references import parse_reference
from app.pipeline.skeleton import build_skeleton
from app.render.docx_acr import render_docx
from app.render.lms import render_gift, render_tests_csv
from app.render.pptx_slides import render_pptx
from app.schemas.ir import Hours
from app.schemas.request import ClarifyingQuestion
from app.skills import list_skills
from app.store import Session, Stage, store

settings = get_settings()
app = FastAPI(
    title="Агент образовательных программ АЦР",
    version="0.1.0",
    description="Сборка программ АЦР и комплекта материалов с автопроверкой",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
#: Метрики снимаются обёрткой вокруг провайдера: пайплайн о них не знает.
provider = MeteredProvider(build_provider())
app.include_router(metrics_router)


def _device(request: Request) -> str:
    """Идентификатор устройства: фронт генерирует его один раз и хранит
    в localStorage, поэтому статистика копится по каждому браузеру отдельно."""
    return (request.headers.get("x-device-id") or "")[:64]

#: Предел на референс: методист прикладывает программу, а не видеоархив.
MAX_REFERENCE_BYTES = 25 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Модели запросов
# --------------------------------------------------------------------------- #
class CreateSessionIn(BaseModel):
    text: str
    references: list[str] = []


class AnswersIn(BaseModel):
    answers: dict[str, str]


class ContentIn(BaseModel):
    test_total: int | None = None
    slides_modules: list[int] | None = None


class TopicPatchIn(BaseModel):
    instruction: str = ""
    regenerate_tests: bool = True


class HoursPatchIn(BaseModel):
    module_hours: dict[str, float] | None = None  # {"M-1": 12, ...}
    topic_hours: dict[str, dict[str, float]] | None = None


def _session_or_404(session_id: str) -> Session:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, f"сессия {session_id} не найдена")
    return session


def _usage_snapshot(session: Session) -> tuple[int, tuple[int, int]]:
    """Счётчики до фазы: разница даст расход именно этой фазы."""
    u = session.usage
    return u.calls, (u.prompt_tokens, u.completion_tokens)


def _record_run(
    session: Session,
    phase: str,
    started_at: str,
    duration_s: float,
    ok: bool,
    calls_before: int,
    tokens_before: tuple[int, int],
    error: str = "",
) -> None:
    u = session.usage
    prompt = u.prompt_tokens - tokens_before[0]
    completion = u.completion_tokens - tokens_before[1]
    s = get_settings()
    # usage.calls считает агрегированные добавления, а не обращения к модели,
    # поэтому реальное число вызовов берём из журнала за время фазы.
    counted = db.one(
        "SELECT COUNT(*) AS n FROM llm_calls WHERE session_id = ? AND ts >= ?",
        (session.id, started_at),
    ).get("n") or (u.calls - calls_before)
    record.run(
        session_id=session.id,
        device_id=session.device_id,
        phase=phase,
        started_at=started_at,
        duration_s=duration_s,
        ok=ok,
        error=error,
        llm_calls=counted,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_rub=prompt / 1000 * s.price_prompt_per_1k
        + completion / 1000 * s.price_completion_per_1k,
        provider=provider.name,
        model=provider.model,
    )


def _state(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "stage": session.stage.value,
        "request": session.request.model_dump(mode="json"),
        "missing": session.request.missing_fields(),
        "questions": [q.model_dump() for q in session.questions],
        "program": session.program.model_dump(mode="json") if session.program else None,
        "report": session.report.model_dump(mode="json") if session.report else None,
        "report_summary": session.report.summary() if session.report else None,
        "events": [e.model_dump(mode="json") for e in session.events],
        "timings": session.timings,
        "phase": session.phase,
        "phase_started_at": (
            session.phase_started_at.isoformat() if session.phase_started_at else None
        ),
        "economics": session.economics(),
        "error": session.error,
        "unparsed": [],
        "reask": {},
    }


# --------------------------------------------------------------------------- #
# Служебные
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "llm_provider": provider.name,
        "llm_model": provider.model or "—",
    }


@app.get("/api/skills")
async def skills() -> list[dict]:
    """Состав навыков агента — для переноса логики в GigaCowork и для защиты."""
    return [
        {"name": s.name, "title": s.title, "description": s.description.strip()}
        for s in list_skills()
    ]


# --------------------------------------------------------------------------- #
# Шаг 1: запрос и уточняющие вопросы
# --------------------------------------------------------------------------- #
@app.post("/api/sessions")
async def create_session(payload: CreateSessionIn, request: Request) -> dict:
    session = store.create()
    session.device_id = _device(request)
    current_session.set(session.id)
    session.references = payload.references
    session.log("intake", "Разбор запроса методиста")
    try:
        intake, usage = await run_intake(
            provider, payload.text, references=session.references
        )
    except Exception as exc:  # noqa: BLE001 — наружу уходит понятная ошибка
        session.stage = Stage.FAILED
        session.error = str(exc)
        store.save(session)
        record.action(
            session_id=session.id, device_id=session.device_id,
            kind="intake_failed", error=str(exc),
        )
        raise HTTPException(502, f"модель недоступна: {exc}") from exc
    session.usage.add(usage)
    session.request = intake.request
    session.questions = intake.questions
    session.stage = Stage.CLARIFYING if intake.questions else Stage.CREATED
    for note in intake.notes:
        session.log("intake", note, level="warning")
    session.log(
        "intake",
        f"Параметры извлечены. Не хватает: "
        f"{', '.join(session.request.missing_fields()) or 'ничего'}",
    )
    store.save(session)
    record.session_created(
        session_id=session.id,
        device_id=session.device_id,
        user_agent=request.headers.get("user-agent", ""),
        request_text=payload.text,
        program_type=session.request.program_type or "",
        total_hours=session.request.total_hours,
        audience=session.request.audience or "",
        has_references=bool(session.references),
        questions_asked=len(session.questions),
        provider=provider.name,
        model=provider.model,
    )
    if session.questions:
        record.stage(
            session_id=session.id, device_id=session.device_id, name="clarifying",
            note=",".join(q.field for q in session.questions),
        )
    return _state(session)


@app.post("/api/references/parse")
async def parse_reference_file(file: UploadFile) -> dict:
    """Разобрать референс до создания сессии.

    Методист прикладывает прошлую программу или профстандарт вместе с запросом,
    поэтому разбор не привязан к сессии: он без состояния и возвращает только
    структуру документа, которую потом передают в POST /api/sessions.
    """
    data = await file.read()
    name = file.filename or "reference"
    if len(data) > MAX_REFERENCE_BYTES:
        raise HTTPException(
            413,
            f"файл больше {MAX_REFERENCE_BYTES // (1024 * 1024)} МБ: {name}",
        )
    try:
        text = parse_reference(name, data)
    except ValueError as exc:
        raise HTTPException(400, f"{name}: {exc}") from exc
    if not text.strip():
        raise HTTPException(
            422, f"{name}: не удалось извлечь структуру — файл пустой или это скан"
        )
    return {
        "filename": name,
        "size_bytes": len(data),
        "chars": len(text),
        "preview": text[:600],
        "text": text,
    }


@app.post("/api/sessions/{session_id}/references")
async def upload_reference(session_id: str, file: UploadFile) -> dict:
    session = _session_or_404(session_id)
    data = await file.read()
    try:
        text = parse_reference(file.filename or "reference", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.references.append(text)
    session.log("intake", f"Референс принят: {file.filename} ({len(text)} символов структуры)")
    store.save(session)
    return {"ok": True, "references": len(session.references), "preview": text[:500]}


@app.post("/api/sessions/{session_id}/answers")
async def answer_questions(session_id: str, payload: AnswersIn) -> dict:
    session = _session_or_404(session_id)
    failed = apply_answers(session.request, payload.answers)
    for field in failed:
        session.log("intake", f"Ответ на «{field}» не распознан, уточните", level="warning")
    missing = session.request.missing_fields()
    session.questions = [q for q in session.questions if q.field in missing]
    # Непонятый ответ не должен останавливать диалог: вопрос задаётся заново,
    # но уже с примером допустимого ответа.
    known = {q.field for q in session.questions}
    for field in failed:
        if field not in known:
            session.questions.append(
                ClarifyingQuestion(field=field, question=reask_text(field))
            )
    session.stage = Stage.CLARIFYING if session.questions or missing else Stage.CREATED
    session.log("intake", f"Ответы приняты. Осталось уточнить: {', '.join(missing) or 'ничего'}")
    store.save(session)
    record.action(
        session_id=session.id, device_id=session.device_id, kind="answers",
        unparsed=failed, missing=missing,
    )
    state = _state(session)
    state["unparsed"] = failed
    state["reask"] = {field: reask_text(field) for field in failed}
    return state


# --------------------------------------------------------------------------- #
# Шаг 2: каркас (гейт человека)
# --------------------------------------------------------------------------- #
@app.post("/api/sessions/{session_id}/skeleton")
async def make_skeleton(session_id: str) -> dict:
    session = _session_or_404(session_id)
    if not session.request.ready():
        raise HTTPException(
            409,
            {
                "error": "не хватает параметров",
                "missing": session.request.missing_fields(),
            },
        )
    current_session.set(session.id)
    async with store.lock(session_id):
        session.start_phase("skeleton")
        phase_started = db.now()
        calls_before, tokens_before = _usage_snapshot(session)
        session.log("skeleton", "Сборка каркаса: цель, компетенции, учебный план")
        try:
            program, usage = await build_skeleton(
                provider, session.request, references=session.references
            )
        except Exception as exc:  # noqa: BLE001
            session.stage = Stage.FAILED
            session.error = str(exc)
            spent = session.finish_phase()
            store.save(session)
            _record_run(session, "skeleton", phase_started, spent, False,
                        calls_before, tokens_before, str(exc))
            raise HTTPException(502, f"не удалось собрать каркас: {exc}") from exc
        session.usage.add(usage)
        session.program = program
        session.report = validate_program(program, stage="skeleton")
        session.stage = Stage.SKELETON
        spent = session.finish_phase()
        session.log(
            "skeleton",
            f"Каркас готов за {spent:.1f} с: {len(program.modules)} модулей, "
            f"{len(program.all_topics())} тем, {program.grand_total:g} ч. "
            f"Автопроверка: ошибок {len(session.report.errors)}, "
            f"предупреждений {len(session.report.warnings)}",
            level="error" if session.report.errors else "info",
        )
        store.save(session)
        _record_run(session, "skeleton", phase_started, spent, True,
                    calls_before, tokens_before)
        record.stage(session_id=session.id, device_id=session.device_id, name="skeleton",
                     note=f"{len(program.modules)} модулей, {program.grand_total:g} ч")
        record.findings(session_id=session.id, check_stage="skeleton", report=session.report)
    return _state(session)


@app.post("/api/sessions/{session_id}/skeleton/hours")
async def patch_hours(session_id: str, payload: HoursPatchIn) -> dict:
    """Правка часов методистом с пересчётом сумм солвером."""
    session = _session_or_404(session_id)
    if session.program is None:
        raise HTTPException(409, "каркас ещё не собран")
    program = session.program
    if payload.module_hours:
        for module in program.modules:
            new_total = payload.module_hours.get(module.id)
            if new_total is None:
                continue
            old = module.hours
            weights = [old.lectures, old.practice, old.labs, old.self_study]
            lk, pr, lb, srs = split_exact(new_total, weights or [1, 1, 0, 1])
            module.hours = Hours(lectures=lk, practice=pr, labs=lb, self_study=srs)
            # Часы тем пересобираем пропорционально прежним весам.
            topic_weights = [t.hours.total or 1 for t in module.topics]
            for kind in ("lectures", "practice", "labs", "self_study"):
                values = rebalance_topics(getattr(module.hours, kind), topic_weights)
                for topic, value in zip(module.topics, values):
                    setattr(topic.hours, kind, value)
    if payload.topic_hours:
        for module in program.modules:
            touched = False
            for topic in module.topics:
                patch = payload.topic_hours.get(topic.id)
                if not patch:
                    continue
                for kind, value in patch.items():
                    if hasattr(topic.hours, kind):
                        setattr(topic.hours, kind, float(value))
                touched = True
            if touched:
                module.hours = Hours.sum([t.hours for t in module.topics])
    session.report = validate_program(
        program, stage="skeleton" if session.stage is Stage.SKELETON else "full"
    )
    session.log("skeleton", "Часы пересчитаны после правки методиста")
    store.save(session)
    record.action(
        session_id=session.id, device_id=session.device_id, kind="hours_patched",
        modules=len(payload.module_hours or {}), topics=len(payload.topic_hours or {}),
    )
    return _state(session)


@app.post("/api/sessions/{session_id}/approve")
async def approve_skeleton(session_id: str) -> dict:
    session = _session_or_404(session_id)
    if session.program is None:
        raise HTTPException(409, "каркас ещё не собран")
    if session.report and session.report.errors:
        record.action(
            session_id=session.id, device_id=session.device_id,
            kind="approve_blocked",
            codes=[f.code for f in session.report.errors],
        )
        raise HTTPException(
            409,
            {
                "error": "каркас не проходит автопроверку",
                "errors": [f.model_dump() for f in session.report.errors],
            },
        )
    session.stage = Stage.APPROVED
    session.log("skeleton", "Каркас утверждён методистом")
    store.save(session)
    record.stage(session_id=session.id, device_id=session.device_id, name="approved")
    return _state(session)


# --------------------------------------------------------------------------- #
# Шаг 3: содержание
# --------------------------------------------------------------------------- #
async def _run_content(session: Session, payload: ContentIn) -> None:
    program = session.program
    assert program is not None
    current_session.set(session.id)
    phase_started = db.now()
    calls_before, tokens_before = _usage_snapshot(session)
    try:
        usage = await build_content(
            provider,
            program,
            test_total=payload.test_total,
            slides_modules=payload.slides_modules,
            progress=lambda stage, message: session.log(stage, message),
        )
        session.usage.add(usage)
        session.report = validate_program(program, stage="full")
        session.stage = Stage.READY
        spent = session.finish_phase()
        session.log(
            "ready",
            f"Комплект готов за {spent:.0f} с: "
            f"{len(program.test_questions)} тестовых вопросов, "
            f"{len(program.seminar_tasks)} заданий, "
            f"{sum(len(p.slides) for p in program.lecture_plans)} слайдов. "
            f"Автопроверка: ошибок {len(session.report.errors)}, "
            f"предупреждений {len(session.report.warnings)}",
        )
        _record_run(session, "content", phase_started, spent, True,
                    calls_before, tokens_before)
        record.stage(session_id=session.id, device_id=session.device_id, name="ready")
        record.findings(session_id=session.id, check_stage="full", report=session.report)
    except Exception as exc:  # noqa: BLE001
        session.stage = Stage.FAILED
        session.error = str(exc)
        spent = session.finish_phase()
        session.log("content", f"Ошибка генерации: {exc}", level="error")
        _record_run(session, "content", phase_started, spent, False,
                    calls_before, tokens_before, str(exc))
        record.stage(session_id=session.id, device_id=session.device_id, name="failed",
                     note=str(exc)[:200])
    store.save(session)


@app.post("/api/sessions/{session_id}/content")
async def make_content(
    session_id: str, payload: ContentIn, background: BackgroundTasks
) -> dict:
    session = _session_or_404(session_id)
    if session.stage not in (Stage.APPROVED, Stage.READY):
        raise HTTPException(409, "сначала утвердите каркас")
    session.stage = Stage.CONTENT
    session.start_phase("content")
    session.log("content", "Генерация содержания комплекта")
    record.stage(session_id=session.id, device_id=session.device_id, name="content")
    store.save(session)
    background.add_task(_run_content, session, payload)
    return _state(session)


@app.post("/api/sessions/{session_id}/topics/{topic_id}/regenerate")
async def regenerate_topic(session_id: str, topic_id: str, payload: TopicPatchIn) -> dict:
    """Точечная доработка одной темы без пересборки комплекта."""
    session = _session_or_404(session_id)
    current_session.set(session.id)
    phase_started = db.now()
    started_perf = time.perf_counter()
    calls_before, tokens_before = _usage_snapshot(session)
    program = session.program
    if program is None:
        raise HTTPException(409, "каркас ещё не собран")
    target = next(
        ((m, t) for m in program.modules for t in m.topics if t.id == topic_id), None
    )
    if target is None:
        raise HTTPException(404, f"тема {topic_id} не найдена")
    module, topic = target
    if payload.instruction:
        topic.title = topic.title  # заголовок меняет методист, инструкция идёт в промпт
        program.meta.direction = program.meta.direction
    async with store.lock(session_id):
        usage = await fill_topic_content(provider, program, module, topic)
        session.usage.add(usage)
        if payload.regenerate_tests:
            program.test_questions = [
                q for q in program.test_questions if q.topic_id != topic.id
            ]
            session.usage.add(await generate_tests(provider, program, [topic]))
            program.seminar_tasks = [
                s for s in program.seminar_tasks if s.topic_id != topic.id
            ]
            session.usage.add(await generate_seminars(provider, program, [topic]))
        if any(p.topic_id == topic.id for p in program.lecture_plans):
            program.lecture_plans = [
                p for p in program.lecture_plans if p.topic_id != topic.id
            ]
            session.usage.add(await generate_lectures(provider, program, [topic]))
        session.report = validate_program(program, stage="full")
        session.log("patch", f"Тема {topic.code} пересобрана, остальной комплект сохранён")
        store.save(session)
        _record_run(
            session, "regenerate", phase_started,
            time.perf_counter() - started_perf, True, calls_before, tokens_before,
        )
        record.action(
            session_id=session.id, device_id=session.device_id,
            kind="topic_regenerated", topic=topic.code,
            instruction=payload.instruction[:200],
        )
    return _state(session)


# --------------------------------------------------------------------------- #
# Состояние и прогресс
# --------------------------------------------------------------------------- #
@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    return _state(_session_or_404(session_id))


@app.get("/api/sessions/{session_id}/events")
async def stream_events(session_id: str) -> StreamingResponse:
    session = _session_or_404(session_id)

    async def generator():
        sent = 0
        while True:
            events = session.events[sent:]
            for event in events:
                yield f"data: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"
            sent += len(events)
            payload = {
                "stage": session.stage.value,
                "report": session.report.summary() if session.report else None,
            }
            yield f"event: stage\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if session.stage in (Stage.READY, Stage.FAILED):
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(generator(), media_type="text/event-stream")


# --------------------------------------------------------------------------- #
# Выгрузки
# --------------------------------------------------------------------------- #
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

def _disposition(name: str, ext: str) -> dict[str, str]:
    """Имя файла с кириллицей: заголовки HTTP кодируются latin-1, поэтому RFC 5987."""
    quoted = quote(f"{name}.{ext}")
    return {
        "Content-Disposition": (
            f'attachment; filename="program.{ext}"; ' f"filename*=UTF-8''{quoted}"
        )
    }



def _exported(
    session: Session,
    request: Request,
    kind: str,
    body: bytes | str,
    media_type: str,
    slug: str,
    ext: str,
) -> Response:
    """Отдать файл и записать факт выгрузки: это финальный шаг воронки."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    record.export(
        session_id=session.id,
        device_id=_device(request) or session.device_id,
        kind=kind,
        size=len(payload),
    )
    return Response(payload, media_type=media_type, headers=_disposition(slug, ext))


@app.get("/api/sessions/{session_id}/export/{kind}")
async def export(session_id: str, kind: str, request: Request) -> Response:
    session = _session_or_404(session_id)
    program = session.program
    if program is None:
        raise HTTPException(409, "нечего выгружать: каркас не собран")
    slug = (program.meta.title or "program")[:60].replace("/", "-").strip()
    if kind == "docx":
        return _exported(session, request, "docx", render_docx(program), DOCX_MIME, slug, "docx")
    if kind == "pptx":
        if not program.lecture_plans:
            raise HTTPException(409, "слайды ещё не сгенерированы")
        return _exported(session, request, "pptx", render_pptx(program), PPTX_MIME, slug, "pptx")
    if kind == "gift":
        return _exported(
            session, request, "gift", render_gift(program),
            "text/plain; charset=utf-8", slug, "gift.txt",
        )
    if kind == "csv":
        return _exported(
            session, request, "csv", render_tests_csv(program),
            "text/csv; charset=utf-8", slug, "tests.csv",
        )
    if kind == "json":
        bundle = {
            "program": program.model_dump(mode="json"),
            "validation": session.report.model_dump(mode="json") if session.report else None,
            "economics": session.economics(),
        }
        return _exported(
            session, request, "json",
            json.dumps(bundle, ensure_ascii=False, indent=2),
            "application/json; charset=utf-8", slug, "json",
        )
    raise HTTPException(400, f"неизвестный формат: {kind}")
