"""Параметры запроса методиста — вход для сборки каркаса."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.ir import ProgramType, StudyForm


class ClarifyingQuestion(BaseModel):
    field: str
    question: str
    answer: str | None = None


class ProgramRequest(BaseModel):
    """Нормализованные параметры. Заполняется навыком intake + ответами методиста."""

    raw_text: str = ""
    title: str | None = None
    program_type: ProgramType | None = None
    program_type_confidence: float = 0.0
    direction: str = ""
    total_hours: float | None = None
    audience: str = ""
    entry_level: str = ""
    study_form: StudyForm | None = None
    uses_dot: bool = True
    attestation_form: str | None = None
    attestation_hours: float | None = None
    duration_note: str = ""
    weeks: int | None = None
    days: int | None = None
    sessions: int | None = None
    hours_per_session: float | None = None
    max_hours_per_day: float | None = None
    min_practice_share: float | None = None
    no_contact_hours: bool = False
    constraints: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    reference_notes: list[str] = Field(default_factory=list)

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if self.program_type is None or self.program_type_confidence < 0.6:
            missing.append("program_type")
        if not self.total_hours:
            missing.append("total_hours")
        if not self.audience:
            missing.append("audience")
        if not self.entry_level:
            missing.append("entry_level")
        if self.study_form is None:
            missing.append("study_form")
        if not self.attestation_form:
            missing.append("attestation_form")
        return missing

    def ready(self) -> bool:
        return not self.missing_fields()


class IntakeResult(BaseModel):
    request: ProgramRequest
    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
