"""Состояние сессий методиста.

Человек в контуре → у сессии есть явные стадии и гейт утверждения каркаса.
Хранилище файловое: для демо и прогона тестовых запросов этого достаточно,
интерфейс заменяется на Postgres без изменений вызывающего кода.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
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
from app.schemas.review import ReviewState


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
    accounted_cost: float | None = None

    def add(self, usage: Usage) -> None:
        if self.accounted_cost is not None or usage.cost_rub is not None:
            from app.llm.models import token_cost
            self.accounted_cost = self.cost_rub(rounded=False) + (usage.cost_rub if usage.cost_rub is not None else token_cost('', usage.prompt_tokens, usage.completion_tokens))
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.calls += 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost_rub(self, *, rounded: bool = True) -> float:
        s = get_settings()
        if self.accounted_cost is not None:
            return round(self.accounted_cost, 2) if rounded else self.accounted_cost
        cost = (self.prompt_tokens * s.price_prompt_per_1k + self.completion_tokens * s.price_completion_per_1k) / 1000
        return round(cost, 2) if rounded else cost


#: Сколько реплик держим в сессии: файл на диске не должен пухнуть,
#: а модели для понимания хватает нескольких последних.
MAX_MESSAGES = get_settings().max_messages


class ChatMessage(BaseModel):
    """Реплика в диалоге методиста с агентом.

    Переписка живёт в сессии, а не во фронте: обновление страницы не должно
    стирать разговор, и модели нужен контекст — «а теперь то же для модуля 3»
    понятно только вместе с предыдущими репликами.
    """

    role: str  # user | bot | sys
    text: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stage: Stage = Stage.CREATED
    model_id: str = ""
    request: ProgramRequest = Field(default_factory=ProgramRequest)
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    program: Program | None = None
    review: ReviewState = Field(default_factory=ReviewState)
    report: ValidationReport | None = None
    usage: UsageRecord = Field(default_factory=UsageRecord)
    events: list[Event] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)
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

    def say(self, role: str, text: str) -> None:
        """Записать реплику. История ограничена: сессия живёт на диске,
        а старые реплики модели всё равно не нужны."""
        self.messages.append(ChatMessage(role=role, text=text))
        if len(self.messages) > MAX_MESSAGES:
            del self.messages[:-MAX_MESSAGES]

    def recent_dialogue(self, limit: int = 8) -> str:
        """Последние реплики для промпта — без служебных сообщений."""
        useful = [m for m in self.messages if m.role in ("user", "bot")][-limit:]
        names = {"user": "методист", "bot": "агент"}
        return "\n".join(f"{names[m.role]}: {m.text}" for m in useful)

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


#: Идентификатор сессии — ровно то, что выдаёт `uuid4().hex[:12]`.
#: Он приходит из URL и подставляется в имя файла, поэтому форма проверяется:
#: иначе `../../etc/passwd` уводит чтение и запись за пределы каталога данных.
_SESSION_ID = re.compile(r"^[0-9a-f]{12}$")


class SessionStore:
    """Сессии в памяти с зеркалом на диске.

    Диск нужен, чтобы перезапуск сервиса не терял работу методиста. Запись
    атомарная: сначала во временный файл рядом, затем переименование — оно
    неделимо на уровне файловой системы, и оборванная запись не оставляет
    наполовину сохранённую программу.
    """

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

    def _path(self, session_id: str) -> Path | None:
        """Путь к файлу сессии или None, если идентификатор не наш."""
        if not _SESSION_ID.match(session_id):
            return None
        return self._dir / f"{session_id}.json"

    def save(self, session: Session) -> None:
        self._sessions[session.id] = session
        path = self._path(session.id)
        if path is None:
            return
        data = session.model_dump_json(indent=2, exclude_none=False)
        # Временный файл создаём в том же каталоге: переименование атомарно
        # только внутри одной файловой системы.
        handle, tmp_name = tempfile.mkstemp(
            dir=self._dir, prefix=f".{session.id}.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fp:
                fp.write(data)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def _load(self, session_id: str) -> Session | None:
        path = self._path(session_id)
        if path is None or not path.exists():
            return None
        try:
            session = Session(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            # Файл повреждён — ведём себя как при отсутствующей сессии.
            # Падать с 500 на чужом битом файле незачем, а сам файл оставляем:
            # он может пригодиться для разбора.
            return None
        self._sessions[session_id] = session
        return session

    def list_recent(self, limit: int = 20) -> list[Session]:
        return sorted(
            self._sessions.values(), key=lambda s: s.created_at, reverse=True
        )[:limit]


store = SessionStore()
