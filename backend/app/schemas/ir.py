"""ProgramIR — единый источник правды для всего комплекта материалов.

Любой рендерер (.docx, .pptx, GIFT, JSON) читает только эту структуру.
LLM заполняет текстовые поля; часы и связи проверяет код (core/).
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator
from app.schemas.preferences import LearningPreferences


# --------------------------------------------------------------------------- #
# Базовые перечисления
# --------------------------------------------------------------------------- #
class ProgramType(str, Enum):
    """Два шаблона АЦР. Определяют разделы, нормативку и выдаваемый документ."""

    DPP_PK = "dpp_pk"  # доп. проф. программа повышения квалификации (273-ФЗ, приказ 499)
    DOP_OBSH = "dop_obsh"  # доп. общеобразовательная общеразвивающая (приказ 629)


class StudyForm(str, Enum):
    FULL_TIME = "ochnaya"
    PART_TIME = "ochno_zaochnaya"
    DISTANT = "zaochnaya"


class OutcomeKind(str, Enum):
    KNOWLEDGE = "знание"
    SKILL = "умение"
    ABILITY = "навык"


class AssessmentKind(str, Enum):
    TEST = "test"
    SEMINAR = "seminar"
    PROJECT = "project"
    INTERVIEW = "interview"
    EXAM = "exam"
    PORTFOLIO = "portfolio"


class VerificationStatus(str, Enum):
    """Кейс запрещает выдуманные источники. Всё неподтверждённое помечается явно."""

    VERIFIED = "verified"  # есть в локальном реестре / подтверждён реквизитами
    UNVERIFIED = "unverified"  # требует проверки методистом — печатается с пометкой
    REJECTED = "rejected"  # не прошёл проверку, в документ не попадает


# --------------------------------------------------------------------------- #
# Часы
# --------------------------------------------------------------------------- #
#: Шаг часов в учебном плане — правило оформления, а не настройка.
#: В образцах АЦР (data/acr/) часы кратны половине академического часа;
#: дробнее учебную нагрузку не планируют. Менять не нужно.
HOUR_STEP = 0.5

#: Виды работы в строке учебного плана, в порядке граф ЛК·ПР·ЛБ·СРС.
HOUR_KINDS = ("lectures", "practice", "labs", "self_study")

#: Допуск сравнения часов: суммы считаются во float.
HOURS_EPS = 0.01


class Hours(BaseModel):
    """Часы по видам работы. Шаг 0.5 ак. ч. Никогда не считается моделью.

    Отрицательные часы структура не принимает ни при создании, ни при
    присваивании: такая строка не может появиться в плане даже по ошибке
    правки, а не только быть замечена автопроверкой потом.
    """

    model_config = ConfigDict(validate_assignment=True)

    lectures: float = 0.0  # ЛК
    practice: float = 0.0  # ПР
    labs: float = 0.0  # ЛБ
    self_study: float = 0.0  # СРС

    @field_validator(*HOUR_KINDS)
    @classmethod
    def _not_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError(f"часы не могут быть отрицательными: {value}")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> float:
        return round(self.lectures + self.practice + self.labs + self.self_study, 1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def contact(self) -> float:
        """Аудиторные часы."""
        return round(self.lectures + self.practice + self.labs, 1)

    def __add__(self, other: "Hours") -> "Hours":
        return Hours(
            lectures=round(self.lectures + other.lectures, 1),
            practice=round(self.practice + other.practice, 1),
            labs=round(self.labs + other.labs, 1),
            self_study=round(self.self_study + other.self_study, 1),
        )

    @staticmethod
    def sum(items: list["Hours"]) -> "Hours":
        acc = Hours()
        for h in items:
            acc = acc + h
        return acc


# --------------------------------------------------------------------------- #
# Нормативная база и источники
# --------------------------------------------------------------------------- #
class LegalAct(BaseModel):
    id: str
    title: str
    requisites: str = ""  # «приказ Минобрнауки России от 01.07.2013 № 499»
    url: str | None = None
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    note: str = ""


class ProfStandard(BaseModel):
    """Профстандарт + коды трудовых функций. Только из реестра, иначе unverified."""

    code: str  # «06.015»
    title: str  # «Специалист по информационным системам»
    approved_by: str = ""  # «приказ Минтруда России от 13.07.2023 № 586н»
    labor_functions: list[str] = Field(default_factory=list)  # «C/14.6 ...»
    url: str | None = None
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    note: str = ""


class Source(BaseModel):
    id: str
    kind: Literal["main", "extra", "internet", "software"] = "main"
    citation: str
    url: str | None = None
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    note: str = ""


# --------------------------------------------------------------------------- #
# Компетенции и результаты обучения
# --------------------------------------------------------------------------- #
class Outcome(BaseModel):
    """Результат обучения. Атом трассировки: тема → РО → оценочное средство."""

    id: str  # «RO-1»
    kind: OutcomeKind
    text: str
    competency_id: str | None = None


class Competency(BaseModel):
    id: str  # «PK-1»
    title: str
    activity: str = ""  # «Виды деятельности» в шаблоне ПК
    prof_standard_code: str | None = None
    labor_function: str | None = None  # «C/14.6»
    outcome_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Структура обучения
# --------------------------------------------------------------------------- #
class Topic(BaseModel):
    id: str  # «T-1.2»
    code: str  # «1.2»
    title: str
    hours: Hours = Field(default_factory=Hours)
    content: str = ""  # наполняется на этапе контента
    activities: str = ""  # «ПР (9 часов): практикум ...»
    outcome_ids: list[str] = Field(default_factory=list)
    assessment_form: str = ""
    # Пожелание методиста к теме («усложнить», «примеры из банков»). Хранится
    # в теме, а не только в запросе на доработку: иначе пожелание, данное до
    # утверждения каркаса, терялось при сборке комплекта.
    instruction: str = ""


class Module(BaseModel):
    id: str  # «M-1»
    index: int
    title: str
    hours: Hours = Field(default_factory=Hours)
    assessment_form: str = ""
    topics: list[Topic] = Field(default_factory=list)
    outcome_ids: list[str] = Field(default_factory=list)

    @property
    def topics_hours(self) -> Hours:
        return Hours.sum([t.hours for t in self.topics])

    def hours_gaps(self) -> dict[str, tuple[float, float]]:
        """Виды работы, где сумма тем не равна строке модуля: {вид: (темы, модуль)}.

        Пустой словарь — строка модуля подтверждена темами. Модуль без тем
        подтвердить нечем, это отдельная проверка (HOURS_NO_TOPICS).
        """
        if not self.topics:
            return {}
        topics = self.topics_hours
        gaps = {}
        for kind in HOUR_KINDS:
            got, want = getattr(topics, kind), getattr(self.hours, kind)
            if abs(got - want) > HOURS_EPS:
                gaps[kind] = (got, want)
        return gaps


class FinalAttestation(BaseModel):
    hours: float = 0.0
    form: str = ""  # «защита проекта», «зачёт по портфолио», «экзамен»
    description: str = ""
    criteria: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Оценочные средства
# --------------------------------------------------------------------------- #
class TestQuestion(BaseModel):
    id: str
    topic_id: str | None = None
    outcome_ids: list[str] = Field(default_factory=list)
    stem: str
    options: list[str] = Field(default_factory=list)
    correct: list[int] = Field(default_factory=list)  # индексы верных вариантов
    explanation: str = ""
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class SeminarTask(BaseModel):
    id: str
    topic_id: str | None = None
    outcome_ids: list[str] = Field(default_factory=list)
    title: str
    statement: str
    deliverable: str = ""
    criteria: list[str] = Field(default_factory=list)


class Slide(BaseModel):
    title: str
    bullets: list[str] = Field(default_factory=list)
    notes: str = ""


class LecturePlan(BaseModel):
    id: str
    topic_id: str
    title: str
    objectives: list[str] = Field(default_factory=list)
    timing: list[str] = Field(default_factory=list)
    slides: list[Slide] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Программа
# --------------------------------------------------------------------------- #
class Schedule(BaseModel):
    weeks: int | None = None
    days: int | None = None
    sessions: int | None = None
    hours_per_session: float | None = None
    max_hours_per_day: float | None = None
    note: str = ""


class ProgramMeta(BaseModel):
    title: str
    program_type: ProgramType
    total_hours: float
    direction: str = ""  # «Анализ данных на Python»
    audience: str = ""
    entry_level: str = ""
    study_form: StudyForm = StudyForm.PART_TIME
    uses_dot: bool = True  # ЭО и ДОТ
    issued_document: str = ""  # удостоверение / сертификат
    level: str = ""  # «базовый»
    profile: str = "техническая"
    organization: str = (
        "Автономная некоммерческая организация дополнительного профессионального "
        "образования «Академия Информационных Технологий»"
    )
    city: str = "Магас"
    year: int = 2026
    # Поля, которые агент обязан оставить пустыми (заполняет человек)
    order_number: str = ""
    order_date: str = ""


class Program(BaseModel):
    """Корневой объект. Сериализуется в JSON как часть выгрузки комплекта."""

    schema_version: str = "1.0"
    learning_preferences: LearningPreferences = Field(default_factory=LearningPreferences)
    meta: ProgramMeta
    annotation: str = ""
    relevance: str = ""
    goal: str = ""
    objectives: list[str] = Field(default_factory=list)
    legal_acts: list[LegalAct] = Field(default_factory=list)
    prof_standards: list[ProfStandard] = Field(default_factory=list)
    competencies: list[Competency] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)
    modules: list[Module] = Field(default_factory=list)
    final_attestation: FinalAttestation = Field(default_factory=FinalAttestation)
    schedule: Schedule = Field(default_factory=Schedule)
    entry_requirements: str = ""
    test_questions: list[TestQuestion] = Field(default_factory=list)
    seminar_tasks: list[SeminarTask] = Field(default_factory=list)
    lecture_plans: list[LecturePlan] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    material_conditions: list[str] = Field(default_factory=list)
    software: list[str] = Field(default_factory=list)

    # ------------------------------ производные ---------------------------- #
    @property
    def modules_hours(self) -> Hours:
        return Hours.sum([m.hours for m in self.modules])

    @property
    def grand_total(self) -> float:
        return round(self.modules_hours.total + self.final_attestation.hours, 1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hours_check(self) -> dict:
        """Сверка часов, встроенная в саму структуру.

        Едет в каждой выгрузке JSON рядом с часами: любой потребитель
        (LMS, проверяющий, другой агент) видит, сходится ли итог с заказанным
        объёмом и подтверждены ли строки модулей темами, не запуская
        автопроверку. Считается из данных при каждой сериализации, поэтому
        устареть не может.
        """
        modules_off = [m.id for m in self.modules if m.hours_gaps()]
        gap = round(self.grand_total - self.meta.total_hours, 1)
        return {
            "declared": self.meta.total_hours,
            "modules": self.modules_hours.total,
            "attestation": self.final_attestation.hours,
            "total": self.grand_total,
            "gap": gap,
            "modules_off": modules_off,
            "ok": abs(gap) <= HOURS_EPS and not modules_off,
        }

    def topic_by_id(self, topic_id: str) -> Topic | None:
        for m in self.modules:
            for t in m.topics:
                if t.id == topic_id:
                    return t
        return None

    def all_topics(self) -> list[Topic]:
        return [t for m in self.modules for t in m.topics]

    def outcome_ids(self) -> list[str]:
        return [o.id for o in self.outcomes]
