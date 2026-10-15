"""Состояние сессий методиста.

Человек в контуре → у сессии есть явные стадии и гейт утверждения каркаса.
Хранилище файловое: для демо и прогона тестовых запросов этого достаточно,
интерфейс заменяется на Postgres без изменений вызывающего кода.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.validate import ValidationReport
from app.llm.base import Usage
from app.schemas.ir import Program
from app.schemas.request import ClarifyingQuestion, ProgramRequest


class Stage(str, Enum):
    CREATED = "created"
    CLARIFYING = "clarifying"  # ждём ответов методиста
    SKELETON = "skeleton"  # каркас собран, ждём утверждения
    APPROVED = "approved"  # методист утвердил каркас
    CONTENT = "content"  # генерируется содержание
    READY = "ready"  # комплект готов
    FAILED = "failed"


class Event(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stage: str
    message: str
    level: str = "info"


class UsageRecord(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def add(self, usage: Usage) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.calls += 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost_rub(self) -> float:
        s = get_settings()
        return round(
            self.prompt_tokens / 1000 * s.price_prompt_per_1k
            + self.completion_tokens / 1000 * s.price_completion_per_1k,
            2,
        )


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stage: Stage = Stage.CREATED
    request: ProgramRequest = Field(default_factory=ProgramRequest)
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    program: Program | None = None
    report: ValidationReport | None = None
    usage: UsageRecord = Field(default_factory=UsageRecord)
    events: list[Event] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    device_id: str = ""
    error: str = ""
    # Сколько секунд заняла каждая фаза и когда начата текущая: методисту важно
    # видеть, что каркас — это минуты, а не часы ручной работы.
    timings: dict[str, float] = Field(default_factory=dict)
    phase: str = ""
    phase_started_at: datetime | None = None

    def log(self, stage: str, message: str, level: str = "info") -> None:
        self.events.append(Event(stage=stage, message=message, level=level))

    def start_phase(self, phase: str) -> None:
        self.phase = phase
        self.phase_started_at = datetime.now(UTC)

    def finish_phase(self) -> float:
        """Закрывает текущую фазу и возвращает её длительность в секундах."""
        if not self.phase or self.phase_started_at is None:
            return 0.0
        elapsed = (datetime.now(UTC) - self.phase_started_at).total_seconds()
        self.timings[self.phase] = round(elapsed, 1)
        self.phase = ""
        self.phase_started_at = None
        return elapsed

    def economics(self) -> dict:
        s = get_settings()
        cost = self.usage.cost_rub()
        saved_hours = s.manual_hours_per_program
        return {
            "tokens": self.usage.total_tokens,
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "llm_calls": self.usage.calls,
            "cost_rub": cost,
            "manual_hours_baseline": saved_hours,
            "manual_cost_rub": round(saved_hours * s.methodist_hour_cost, 2),
            "savings_rub": round(saved_hours * s.methodist_hour_cost - cost, 2),
        }


class SessionStore:
    def __init__(self, data_dir: str | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._dir = Path(data_dir or get_settings().data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def create(self) -> Session:
        session = Session()
        self._sessions[session.id] = session
        self._locks[session.id] = asyncio.Lock()
        return session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            session = self._load(session_id)
        return session

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def save(self, session: Session) -> None:
        self._sessions[session.id] = session
        path = self._dir / f"{session.id}.json"
        path.write_text(
            session.model_dump_json(indent=2, exclude_none=False), encoding="utf-8"
        )

    def _load(self, session_id: str) -> Session | None:
        path = self._dir / f"{session_id}.json"
        if not path.exists():
            return None
        session = Session(**json.loads(path.read_text(encoding="utf-8")))
        self._sessions[session_id] = session
        return session

    def list_recent(self, limit: int = 20) -> list[Session]:
        return sorted(
            self._sessions.values(), key=lambda s: s.created_at, reverse=True
        )[:limit]


store = SessionStore()
