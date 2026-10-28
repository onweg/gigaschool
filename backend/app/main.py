"""HTTP API агента.

Единый эндпоинт решения (требование кейса): все шаги агента доступны через
REST, прогресс — через SSE, выгрузки — через /export. Логика шагов живёт
в app/pipeline, здесь только транспорт и управление стадиями сессии.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.config import (
    ConfigError,
    build_provider,
    check_config,
    describe_config,
    get_settings,
    host_rule,
)

# Конфигурацию проверяем до импорта остальных модулей: хранилище сессий и
# метрики читают настройки при импорте, и ошибка в .env всплыла бы там —
# стеком вызовов посреди демонстрации, а не объяснением.
def _refuse_to_start(exc: Exception) -> None:
    print("\n  Не удалось запустить агента.\n", file=sys.stderr)
    print(f"  {exc}\n", file=sys.stderr)
    print("  Проверьте .env или переменные окружения и запустите снова.\n", file=sys.stderr)
    raise SystemExit(2) from None


try:
    settings = get_settings()
except ConfigError as exc:
    _refuse_to_start(exc)

from app.llm.base import LLMError, redact  # noqa: E402
from app.llm.models import ModelRouter, selected_model
from app.llm.catalog import YANDEX_HOST
from app.llm.metered import MeteredProvider, current_session  # noqa: E402
from app.metrics import db, record
from app.metrics.api import router as metrics_router
from app.core.hours import rebalance_topics, split_exact
from app.core.validate import validate_program
from app.pipeline.adapt import adapt_program
from app.pipeline.content import (
    build_content,
    fill_topic_content,
    generate_lectures,
    generate_seminars,
    generate_tests,
)
from app.pipeline.intake import apply_answers, questions_for, reask_text, run_intake
from app.pipeline.references import parse_reference
from app.pipeline.refresh import content_from_text, find_outdated, program_content
from app.pipeline.sources_review import pending as sources_pending, review as review_source
from app.pipeline.skeleton import build_skeleton
from app.render.docx_acr import render_docx
from app.render.lms import render_gift, render_tests_csv
from app.render.pptx_slides import render_pptx
from app.schemas.ir import Hours, ProgramType as ProgramTypeIn, StudyForm as StudyFormIn
from app.schemas.ir import HOUR_KINDS, HOUR_STEP, Program
from app.schemas.request import ClarifyingQuestion, ProgramRequest as ClarifyingRequest
from app.skills import list_skills
from app.store import Session, Stage, store

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
try:
    _base_provider = build_provider()
except ConfigError as exc:
    _refuse_to_start(exc)

_host_rule = (
    host_rule(settings.llm_base_url, (YANDEX_HOST,) if settings.llm_profile == "yandex_dev" else ())
    if getattr(_base_provider, "host", None)
    else None
)

print("Агент образовательных программ АЦР", file=sys.stderr)
for _line in describe_config():
    print(f"  {_line}", file=sys.stderr)
for _warning in check_config():
    print(f"  ! {_warning}", file=sys.stderr)
if _base_provider.name.startswith("mock"):
    print(
        "  ! офлайн-заглушка: структура и часы настоящие, содержание — болванки",
        file=sys.stderr,
    )
print("", file=sys.stderr)

#: Метрики снимаются обёрткой вокруг провайдера: пайплайн о них не знает.
model_router = ModelRouter(_base_provider, profile=settings.llm_profile, configured_model=settings.llm_model)
provider = MeteredProvider(model_router)
_model_jobs: dict[str, int] = {}


@app.middleware("http")
async def bind_request_model(request: Request, call_next):
    match = re.match(r"^/api/sessions/([0-9a-f]{12})(?:/|$)", request.url.path)
    session_id = match.group(1) if match else ""
    session = store.get(session_id) if session_id else None
    token = selected_model.set(session.model_id if session else "")
    job = bool(session_id and request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.endswith("/model"))
    if job:
        _model_jobs[session_id] = _model_jobs.get(session_id, 0) + 1
    try:
        return await call_next(request)
    finally:
        selected_model.reset(token)
        if job:
            _model_jobs[session_id] -= 1
            if not _model_jobs[session_id]:
                del _model_jobs[session_id]


def _bind_model(session: Session, model_id: str | None = None):
    try:
        chosen = model_router.resolve(model_id or session.model_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if session.model_id and session.model_id != chosen and (_model_jobs.get(session.id) or session.phase or session.stage == Stage.CONTENT or store.lock(session.id).locked()):
        raise HTTPException(409, "дождитесь завершения текущей операции перед сменой модели")
    session.model_id = chosen
    selected_model.set(chosen)

app.include_router(metrics_router)


def _device(request: Request) -> str:
    """Идентификатор устройства: фронт генерирует его один раз и хранит
    в localStorage, поэтому статистика копится по каждому браузеру отдельно."""
    return (request.headers.get("x-device-id") or "")[:64]

#: Предел на референс: методист прикладывает программу, а не видеоархив.
MAX_REFERENCE_BYTES = settings.max_reference_mb * 1024 * 1024


# --------------------------------------------------------------------------- #
# Модели запросов
# --------------------------------------------------------------------------- #
class CreateSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str | None = None
    text: str
    references: list[str] = []
    answers: dict[str, str] = Field(default_factory=dict)


class AnswersIn(BaseModel):
    answers: dict[str, str]


class ContentIn(BaseModel):
    test_total: int | None = None
    slides_modules: list[int] | None = None


class GenerateIn(BaseModel):
    """Единый вход для машинного прогона: запрос целиком одним вызовом.

    Либо `text` на первом обращении, либо `session_id` + `answers` — чтобы
    дослать ответы на уточняющие вопросы и довести ту же сессию до комплекта.
    """

    model_config = ConfigDict(extra="forbid")
    text: str = ""
    session_id: str | None = None
    model_id: str | None = None
    answers: dict[str, str] = {}
    references: list[str] = []
    test_total: int | None = None
    slides_modules: list[int] | None = None


class SourceReviewIn(BaseModel):
    """Решение методиста по неподтверждённому источнику."""

    kind: str  # prof_standard | legal_act | source
    key: str  # код профстандарта или id источника
    action: str  # confirm | remove


class AdaptIn(BaseModel):
    """Новые условия для готовой программы. Пустые поля берутся из исходной."""

    audience: str = ""
    total_hours: float | None = None
    program_type: ProgramTypeIn | None = None
    study_form: StudyFormIn | None = None
    entry_level: str = ""
    title: str = ""
    attestation_form: str = ""


class TopicPatchIn(BaseModel):
    """Точечная доработка темы. `instruction` — пожелание методиста обычным
    текстом: «усложни тест», «меньше теории», «добавь примеры из банков»."""

    instruction: str = ""
    title: str = ""  # новое название темы, если методист его меняет
    regenerate_tests: bool = True


class HoursPatchIn(BaseModel):
    module_hours: dict[str, float] | None = None  # {"M-1": 12, ...}
    topic_hours: dict[str, dict[str, float]] | None = None


def _offline() -> bool:
    """Сервис работает на офлайн-заглушке, а не на модели."""
    return provider.name.startswith("mock")


def _model_failure(exc: Exception) -> str:
    """Отличить «модель не ответила» от «ответила, но разобрать не вышло».

    Раньше любой сбой подписывался «модель недоступна» — даже когда модель
    ответила, а упал разбор её ответа. Человек ждал минуту и повторял зря.
    """
    if isinstance(exc, LLMError):
        return f"модель недоступна: {_safe(exc)}"
    return f"не удалось разобрать ответ модели: {_safe(exc)}"


def _safe(exc: BaseException) -> str:
    """Текст ошибки, который можно показать человеку и записать в сессию.

    Ошибки провайдера уже очищены, но падать может и не провайдер — ключ не
    должен доехать до интерфейса ни при каком сбое.
    """
    return redact(str(exc), settings.llm_api_key)


def _session_or_404(session_id: str) -> Session:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, f"сессия {session_id} не найдена")
    return session


def _usage_snapshot(session: Session) -> tuple[int, tuple[int, int, float]]:
    """Счётчики до фазы: разница даст расход именно этой фазы."""
    u = session.usage
    return u.calls, (u.prompt_tokens, u.completion_tokens, u.cost_rub(rounded=False))


def _record_run(
    session: Session,
    phase: str,
    started_at: str,
    duration_s: float,
    ok: bool,
    calls_before: int,
    tokens_before: tuple[int, int, float],
    error: str = "",
) -> None:
    u = session.usage
    prompt = u.prompt_tokens - tokens_before[0]
    completion = u.completion_tokens - tokens_before[1]
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
        cost_rub=u.cost_rub(rounded=False) - tokens_before[2],
        provider=provider.name,
        model=provider.model,
    )


def _request_from_program(program: Program) -> ClarifyingRequest:
    """Восстановить параметры запроса по собранной программе.

    Нужно адаптации: новая сессия должна выглядеть как обычная — с теми же
    параметрами, иначе следующий шаг (генерация содержания) не поймёт,
    для кого пишет.
    """
    meta = program.meta
    return ClarifyingRequest(
        **program.learning_preferences.model_dump(),
        title=meta.title,
        program_type=meta.program_type,
        program_type_confidence=1.0,
        direction=meta.direction,
        total_hours=meta.total_hours,
        audience=meta.audience,
        entry_level=meta.entry_level,
        study_form=meta.study_form,
        uses_dot=meta.uses_dot,
        attestation_form=program.final_attestation.form or None,
        attestation_hours=program.final_attestation.hours or None,
    )


def _state(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "model_id": session.model_id or model_router.default,
        "stage": session.stage.value,
        "request": session.request.model_dump(mode="json"),
        "missing": session.request.missing_fields(),
        "questions": [q.model_dump() for q in session.questions],
        "program": session.program.model_dump(mode="json") if session.program else None,
        "report": session.report.model_dump(mode="json") if session.report else None,
        "report_summary": session.report.summary() if session.report else None,
        "events": [e.model_dump(mode="json") for e in session.events],
        "messages": [m.model_dump(mode="json") for m in session.messages],
        "timings": session.timings,
        "phase": session.phase,
        "phase_started_at": (
            session.phase_started_at.isoformat() if session.phase_started_at else None
        ),
        "economics": session.economics(),
        # Работа на заглушке не должна выглядеть как настоящая: признак едет
        # в каждом ответе, и интерфейс показывает его плашкой.
        "offline_stub": _offline(),
        "error": session.error,
        "unparsed": [],
        "reask": {},
    }


from app.routes.review import review_router

app.include_router(review_router(provider, _session_or_404, _state, _safe))


# --------------------------------------------------------------------------- #
# Служебные
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict:
    offline = provider.name.startswith("mock")
    return {
        "status": "ok",
        "llm_profile": model_router.profile,
        "llm_provider": provider.name,
        "llm_model": provider.model or "—",
        # Явный признак: на заглушке содержание программ не генерируется,
        # и это должно быть видно снаружи, а не выясняться по текстам.
        "offline_stub": offline,
        # Куда ходит агент и каким правилом это разрешено — проверить
        # ограничение хостов можно снаружи, без доступа к .env.
        "llm_host": getattr(_base_provider, "host", None),
        "llm_host_rule": _host_rule,
        "note": (
            "офлайн-заглушка: структура и часы настоящие, содержание — болванки"
            if offline
            else "работа на модели"
        ),
    }


@app.get("/api/models")
async def model_catalog() -> dict:
    return {"models": list(model_router.options.values()), "default_id": model_router.default,
            "offline_stub": model_router.offline, "profile": model_router.profile,
            "selection_enabled": len(model_router.options) > 1}


class ModelChoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str


@app.post("/api/sessions/{session_id}/model")
async def choose_session_model(session_id: str, payload: ModelChoiceIn) -> dict:
    session = _session_or_404(session_id)
    if _model_jobs.get(session_id) or session.phase or session.stage == Stage.CONTENT or store.lock(session_id).locked():
        raise HTTPException(409, "дождитесь завершения текущей операции перед сменой модели")
    previous = session.model_id or model_router.default
    _bind_model(session, payload.model_id)
    if previous != session.model_id:
        session.log("model", f"Модель для следующих операций: {session.model_id}. Готовые материалы сохранены.")
        record.action(session_id=session.id, device_id=session.device_id, kind="model_selected", model=session.model_id)
    store.save(session)
    return _state(session)


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
    _bind_model(session, payload.model_id)
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
        session.error = _safe(exc)
        store.save(session)
        record.action(
            session_id=session.id, device_id=session.device_id,
            kind="intake_failed", error=_safe(exc),
        )
        raise HTTPException(502, _model_failure(exc)) from exc
    session.usage.add(usage)
    session.request = intake.request
    failed = apply_answers(session.request, payload.answers)
    session.questions = questions_for(session.request)
    for field in failed:
        if field not in {q.field for q in session.questions}:
            session.questions.append(ClarifyingQuestion(field=field, question=reask_text(field)))
    session.stage = Stage.CLARIFYING if session.questions else Stage.CREATED
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
    # Заново берём недостающие: шестой вопрос не должен теряться после
    # ответа на первую порцию из пяти.
    session.questions = questions_for(session.request)
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
            session.error = _safe(exc)
            spent = session.finish_phase()
            store.save(session)
            _record_run(session, "skeleton", phase_started, spent, False,
                        calls_before, tokens_before, _safe(exc))
            raise HTTPException(502, f"не удалось собрать каркас: {_safe(exc)}") from exc
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


def _check_hours_patch(payload: HoursPatchIn) -> None:
    """Отказать в правке, которая заведомо ломает план, — до того как она
    тронет программу. Иначе половина правки применилась бы, а вторая упала."""
    for module_id, value in (payload.module_hours or {}).items():
        if value <= 0:
            raise HTTPException(400, f"{module_id}: часы модуля должны быть больше нуля")
    for topic_id, patch in (payload.topic_hours or {}).items():
        for kind, value in patch.items():
            if kind not in HOUR_KINDS:
                raise HTTPException(
                    400, f"{topic_id}: «{kind}» — не вид работы (допустимо: {', '.join(HOUR_KINDS)})"
                )
            if value < 0:
                raise HTTPException(400, f"{topic_id}: часы не могут быть отрицательными")
            if abs(value / HOUR_STEP - round(value / HOUR_STEP)) > 1e-9:
                raise HTTPException(400, f"{topic_id}: часы задаются с шагом {HOUR_STEP:g} ч")


@app.post("/api/sessions/{session_id}/skeleton/hours")
async def patch_hours(session_id: str, payload: HoursPatchIn) -> dict:
    """Правка часов методистом с пересчётом сумм солвером."""
    session = _session_or_404(session_id)
    if session.program is None:
        raise HTTPException(409, "каркас ещё не собран")
    program = session.program
    _check_hours_patch(payload)
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
    # Утверждается каркас, поэтому и проверяется он по правилам каркаса.
    # Отчёт мог остаться от проверки полного комплекта (например, после
    # правки темы), и тогда «нет тестов на РО» блокировало кнопку на этапе,
    # где тестов ещё и быть не должно.
    session.report = validate_program(session.program, stage="skeleton")
    if session.report.errors:
        store.save(session)
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
    selected_model.set(session.model_id or model_router.default)
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
        session.error = _safe(exc)
        spent = session.finish_phase()
        session.log("content", f"Ошибка генерации: {_safe(exc)}", level="error")
        _record_run(session, "content", phase_started, spent, False,
                    calls_before, tokens_before, _safe(exc))
        record.stage(session_id=session.id, device_id=session.device_id, name="failed",
                     note=_safe(exc)[:200])
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
    # Методист может заодно переименовать тему: «замени тему 2.3 на SQL-окна».
    if payload.title:
        topic.title = payload.title.strip()
    if payload.instruction.strip():
        topic.instruction = payload.instruction.strip()[:500]
    # До утверждения каркаса содержания ещё нет: тратить запрос к модели
    # незачем, а тесты одной темы сломали бы проверку покрытия всего каркаса.
    # Пожелание запоминаем — сборка комплекта учтёт его для этой темы.
    if session.stage in (Stage.SKELETON, Stage.CLARIFYING, Stage.CREATED):
        session.report = validate_program(program, stage="skeleton")
        session.log("patch", f"Пожелание к теме {topic.code} учтём при сборке комплекта")
        store.save(session)
        record.action(
            session_id=session.id, device_id=session.device_id,
            kind="topic_wish_saved", topic=topic.code,
            instruction=payload.instruction[:200],
        )
        return _state(session)
    async with store.lock(session_id):
        usage = await fill_topic_content(
            provider, program, module, topic, instruction=payload.instruction
        )
        session.usage.add(usage)
        if payload.regenerate_tests:
            program.test_questions = [
                q for q in program.test_questions if q.topic_id != topic.id
            ]
            session.usage.add(
                await generate_tests(
                    provider, program, [topic], instruction=payload.instruction
                )
            )
            program.seminar_tasks = [
                s for s in program.seminar_tasks if s.topic_id != topic.id
            ]
            session.usage.add(
                await generate_seminars(
                    provider, program, [topic], instruction=payload.instruction
                )
            )
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


# --------------------------------------------------------------------------- #
# Проверка источников методистом
# --------------------------------------------------------------------------- #
@app.get("/api/sessions/{session_id}/sources/pending")
async def sources_to_check(session_id: str) -> dict:
    """Что агент не смог подтвердить сам — и где это проверить."""
    session = _session_or_404(session_id)
    if session.program is None:
        raise HTTPException(409, "программа ещё не собрана")
    return {"items": sources_pending(session.program)}


@app.post("/api/sessions/{session_id}/sources/review")
async def sources_review(session_id: str, payload: SourceReviewIn) -> dict:
    """Подтвердить источник после проверки или убрать его из программы.

    После решения автопроверка пересчитывается, и замечание исчезает из
    отчёта и из приложения к документу.
    """
    session = _session_or_404(session_id)
    if session.program is None:
        raise HTTPException(409, "программа ещё не собрана")
    async with store.lock(session_id):
        try:
            message = review_source(session.program, payload.kind, payload.key, payload.action)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        stage = "full" if session.program.test_questions else "skeleton"
        session.report = validate_program(session.program, stage=stage)
        session.log("sources", message)
        store.save(session)
        record.action(
            session_id=session.id, device_id=session.device_id, kind="source_review",
            source_kind=payload.kind, action=payload.action,
        )
    state = _state(session)
    state["pending_sources"] = sources_pending(session.program)
    return state


# --------------------------------------------------------------------------- #
# Актуализация программы
# --------------------------------------------------------------------------- #
def _refresh_response(report, usage) -> dict:  # noqa: ANN001
    """Ответ с честной оговоркой о границах проверки."""
    return {
        "verdict": report.verdict,
        "findings": [
            {**f.model_dump(), "severity_word": f.word} for f in report.findings
        ],
        "summary": {
            "outdated": len(report.outdated),
            "missing": len(report.missing),
            "total": len(report.findings),
        },
        "disclaimer": (
            "Актуальность определена по знаниям модели и требует проверки "
            "методистом: реестра устаревших технологий не существует"
        ),
        "tokens": usage.total,
    }


@app.post("/api/sessions/{session_id}/refresh")
async def refresh_session(session_id: str) -> dict:
    """Проверить собранную программу на устаревшие темы и инструменты."""
    session = _session_or_404(session_id)
    if session.program is None:
        raise HTTPException(409, "нечего проверять: программа ещё не собрана")
    current_session.set(session.id)
    program = session.program
    try:
        report, usage = await find_outdated(
            provider,
            program_content(program),
            title=program.meta.title,
            direction=program.meta.direction,
            audience=program.meta.audience,
            year=str(program.meta.year),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"не удалось проверить актуальность: {_safe(exc)}") from exc

    session.usage.add(usage)
    for finding in report.findings:
        where = f"тема {finding.topic_code}: " if finding.topic_code else ""
        session.log(
            "refresh",
            f"{finding.word} — {where}{finding.what}"
            + (f" → {finding.replacement}" if finding.replacement else ""),
            level="warning" if finding.severity == "outdated" else "info",
        )
    session.say("bot", f"Проверка актуальности: {report.verdict}")
    store.save(session)
    record.action(
        session_id=session.id, device_id=session.device_id, kind="refresh",
        outdated=len(report.outdated), total=len(report.findings),
    )
    return _refresh_response(report, usage)


@app.post("/api/refresh/upload")
async def refresh_uploaded(file: UploadFile) -> dict:
    """Проверить чужую программу из файла: .docx, .pdf или текст.

    Старая программа может быть написана задолго до появления агента и в
    системе её нет — методисту достаточно принести файл.
    """
    data = await file.read()
    if len(data) > MAX_REFERENCE_BYTES:
        raise HTTPException(413, "файл больше 25 МБ")
    try:
        text = parse_reference(file.filename or "program", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    try:
        report, usage = await find_outdated(
            provider, content_from_text(text), title=file.filename or ""
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"не удалось проверить актуальность: {_safe(exc)}") from exc

    return _refresh_response(report, usage)


# --------------------------------------------------------------------------- #
# Адаптация готовой программы
# --------------------------------------------------------------------------- #
@app.post("/api/sessions/{session_id}/adapt")
async def adapt(session_id: str, payload: AdaptIn, request: Request) -> dict:
    """Переработать готовую программу под другую аудиторию или объём.

    Требование кейса «сверх плана». Исходная программа остаётся нетронутой:
    по ней ещё учатся, а методисту нужны обе. Поэтому адаптация создаётся
    отдельной сессией — со своим каркасом, автопроверкой и выгрузками.
    """
    source = _session_or_404(session_id)
    if source.program is None:
        raise HTTPException(409, "нечего адаптировать: программа ещё не собрана")

    changes = {
        key: value
        for key, value in payload.model_dump(exclude_none=True).items()
        if value != ""
    }
    if not changes:
        raise HTTPException(400, "не указано, что менять: аудитория, объём, тип или форма")

    target = store.create()
    _bind_model(target, source.model_id)
    target.device_id = _device(request)
    current_session.set(target.id)
    target.references = source.references
    target.say("sys", f"Адаптация программы «{source.program.meta.title}»")
    target.start_phase("adapt")
    phase_started = db.now()
    calls_before, tokens_before = _usage_snapshot(target)

    try:
        program, notes, usage = await adapt_program(provider, source.program, changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        target.stage = Stage.FAILED
        target.error = _safe(exc)
        spent = target.finish_phase()
        store.save(target)
        _record_run(target, "adapt", phase_started, spent, False,
                    calls_before, tokens_before, _safe(exc))
        raise HTTPException(502, f"не удалось адаптировать программу: {_safe(exc)}") from exc

    target.usage.add(usage)
    target.program = program
    target.request = _request_from_program(program)
    target.report = validate_program(program, stage="skeleton")
    target.stage = Stage.SKELETON
    spent = target.finish_phase()
    for note in notes:
        target.log("adapt", note)
    target.say(
        "bot",
        f"Программа адаптирована за {spent:.1f} с: "
        + "; ".join(notes[:3])
        + f". Модулей {len(program.modules)}, {program.grand_total:g} ч.",
    )
    store.save(target)
    _record_run(target, "adapt", phase_started, spent, True, calls_before, tokens_before)
    record.stage(session_id=target.id, device_id=target.device_id, name="skeleton",
                 note=f"адаптация из {source.id}")
    record.findings(session_id=target.id, check_stage="skeleton", report=target.report)

    state = _state(target)
    state["adapted_from"] = source.id
    state["changes"] = notes
    return state


# --------------------------------------------------------------------------- #
# Единый вход для машинной проверки
# --------------------------------------------------------------------------- #
@app.post("/api/generate")
async def generate(payload: GenerateIn, request: Request) -> dict:
    """Весь путь одним вызовом: запрос → каркас → содержание → автопроверка.

    Кейс требует единого эндпоинта для прогона на наборе тестовых запросов и
    одновременно требует уточняющих вопросов, когда в запросе не хватает данных.
    Поэтому адрес один, а поведений два:

    * данных не хватает → `status: "need_answers"` + `session_id` и вопросы;
      вызывающий отвечает вторым обращением с этим `session_id` и `answers`;
    * данных хватает → `status: "ready"`, комплект и ссылки на выгрузки.

    Шаг утверждения каркаса человеком здесь проходится автоматически, о чём
    честно сказано в ответе полем `skeleton_auto_approved`. Интерфейс методиста
    по-прежнему идёт через /api/sessions/* и спрашивает разрешение у человека.
    """
    if payload.session_id:
        session = _session_or_404(payload.session_id)
        _bind_model(session, payload.model_id)
        if payload.answers:
            unparsed = apply_answers(session.request, payload.answers)
            if unparsed:
                return {
                    "status": "need_answers",
                    "session_id": session.id,
                    "unparsed": unparsed,
                    "questions": [
                        {"field": f, "question": reask_text(f)} for f in unparsed
                    ],
                }
    elif payload.text.strip():
        session = store.create()
        _bind_model(session, payload.model_id)
        session.device_id = _device(request)
        current_session.set(session.id)
        session.references = payload.references
        try:
            intake, usage = await run_intake(
                provider, payload.text, references=session.references
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, _model_failure(exc)) from exc
        session.usage.add(usage)
        session.request = intake.request
        session.questions = intake.questions
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
        if payload.answers:
            apply_answers(session.request, payload.answers)
    else:
        raise HTTPException(400, "нужен text для нового запроса или session_id для продолжения")

    missing = session.request.missing_fields()
    if missing:
        questions = {q.field: q.question for q in session.questions}
        session.stage = Stage.CLARIFYING
        store.save(session)
        return {
            "status": "need_answers",
            "session_id": session.id,
            "missing": missing,
            "questions": [
                {"field": f, "question": questions.get(f) or reask_text(f)} for f in missing
            ],
        }

    current_session.set(session.id)
    async with store.lock(session.id):
        session.start_phase("skeleton")
        phase_started = db.now()
        calls_before, tokens_before = _usage_snapshot(session)
        try:
            program, usage = await build_skeleton(
                provider, session.request, references=session.references
            )
        except Exception as exc:  # noqa: BLE001
            session.stage = Stage.FAILED
            session.error = _safe(exc)
            spent = session.finish_phase()
            store.save(session)
            _record_run(session, "skeleton", phase_started, spent, False,
                        calls_before, tokens_before, _safe(exc))
            raise HTTPException(502, f"не удалось собрать каркас: {_safe(exc)}") from exc
        session.usage.add(usage)
        session.program = program
        session.report = validate_program(program, stage="skeleton")
        skeleton_errors = [f.code for f in session.report.errors]
        spent = session.finish_phase()
        session.stage = Stage.APPROVED
        session.log(
            "skeleton",
            f"Каркас собран за {spent:.1f} с и утверждён автоматически "
            f"(машинный прогон): {len(program.modules)} модулей, {program.grand_total:g} ч",
        )
        store.save(session)
        _record_run(session, "skeleton", phase_started, spent, True,
                    calls_before, tokens_before)
        record.stage(session_id=session.id, device_id=session.device_id, name="skeleton",
                     note=f"{len(program.modules)} модулей, {program.grand_total:g} ч")
        record.findings(session_id=session.id, check_stage="skeleton", report=session.report)

        session.start_phase("content")
        await _run_content(
            session,
            ContentIn(test_total=payload.test_total, slides_modules=payload.slides_modules),
        )

    if session.stage is Stage.FAILED:
        raise HTTPException(502, f"не удалось собрать содержание: {session.error}")

    base = f"/api/sessions/{session.id}/export"
    state = _state(session)
    return {
        "status": "ready",
        "session_id": session.id,
        "skeleton_auto_approved": True,
        "offline_stub": _offline(),
        "skeleton_errors": skeleton_errors,
        "program": session.program.model_dump(mode="json"),
        "report": session.report.model_dump(mode="json") if session.report else None,
        # errors/warnings в ValidationReport — вычисляемые, в JSON их нет.
        # Прогону нужен однозначный вердикт, поэтому отдаём сводку отдельно.
        "summary": {
            "ok": bool(session.report and session.report.ok),
            "errors": [f.code for f in session.report.errors] if session.report else [],
            "warnings": [f.code for f in session.report.warnings] if session.report else [],
            "total_hours": session.program.grand_total,
            "modules": len(session.program.modules),
            "topics": len(session.program.all_topics()),
            "test_questions": len(session.program.test_questions),
            "seminar_tasks": len(session.program.seminar_tasks),
            "slides": sum(len(p.slides) for p in session.program.lecture_plans),
        },
        "economics": session.economics(),
        "timings": state.get("timings", {}),
        "exports": {kind: f"{base}/{kind}" for kind in ("docx", "pptx", "gift", "csv", "json")},
    }


@app.get("/api/sessions/{session_id}/export/{kind}")
async def export(session_id: str, kind: str, request: Request) -> Response:
    session = _session_or_404(session_id)
    program = session.program
    if program is None:
        raise HTTPException(409, "нечего выгружать: каркас не собран")
    slug = (program.meta.title or "program")[:60].replace("/", "-").strip()
    if kind == "docx":
        # Отчёт автопроверки едет вместе с документом: методист открывает
        # файл отдельно от интерфейса и должен видеть, что проверить.
        return _exported(
            session, request, "docx",
            render_docx(program, session.report, offline=_offline()), DOCX_MIME, slug, "docx",
        )
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
        report = session.report
        bundle = {
            "program": program.model_dump(mode="json"),
            "validation": report.model_dump(mode="json") if report else None,
            # Отдельная сводка: в findings серьёзность лежит кодом, а принимать
            # решение по выгрузке нужно быстро и без чтения всего списка.
            "validation_summary": {
                "ok": bool(report and report.ok),
                "errors": len(report.errors) if report else 0,
                "warnings": len(report.warnings) if report else 0,
                "must_fix": [
                    {"code": f.code, "message": f.message, "hint": f.hint}
                    for f in (report.errors if report else [])
                ],
                "check_yourself": [
                    {"code": f.code, "message": f.message, "hint": f.hint}
                    for f in (report.warnings if report else [])
                ],
                "legend": {
                    "errors": "программа не может быть утверждена, пока это не исправлено",
                    "warnings": "агент не смог подтвердить сам — нужен взгляд методиста",
                },
            },
            "economics": session.economics(),
            "offline_stub": _offline(),
        }
        return _exported(
            session, request, "json",
            json.dumps(bundle, ensure_ascii=False, indent=2),
            "application/json; charset=utf-8", slug, "json",
        )
    raise HTTPException(400, f"неизвестный формат: {kind}")


# --------------------------------------------------------------------------- #
# Статика фронта
# --------------------------------------------------------------------------- #
# Среда хакатона ждёт одно приложение на 0.0.0.0:8000, и docker там может быть
# недоступен. Если рядом лежит собранный фронт, отдаём его этим же процессом —
# тогда `uvicorn app.main:app --host 0.0.0.0 --port 8000` поднимает сразу сайт
# и API. Монтируется последним, чтобы не перехватывать /api/*.
_static = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if (_static / "index.html").exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="frontend")
