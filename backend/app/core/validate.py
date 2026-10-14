"""Автопроверка комплекта — то, по чему АЦР принимает решение.

Каждая проверка воспроизводит пункт эталонного чек-листа кейса. Отчёт
отдаётся методисту и одновременно служит внутренним гейтом: при ERROR
агент не переходит к генерации содержания.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.ir import (
    HOUR_STEP,
    AssessmentKind,
    Hours,
    Program,
    ProgramType,
    VerificationStatus,
)

EPS = 1e-6


class Severity(str, Enum):
    ERROR = "error"  # нарушен инвариант чек-листа — программа вернётся на доработку
    WARNING = "warning"  # требует внимания методиста
    INFO = "info"


class Finding(BaseModel):
    code: str
    severity: Severity
    message: str
    path: str = ""  # «modules[1].topics[2].hours»
    hint: str = ""


class ValidationReport(BaseModel):
    findings: list[Finding] = Field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, code: str, severity: Severity, message: str, **kw: str) -> None:
        self.findings.append(
            Finding(code=code, severity=severity, message=message, **kw)
        )

    def summary(self) -> dict:
        return {
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "checks": len(self.findings),
        }


# --------------------------------------------------------------------------- #
# 1. Арифметика часов
# --------------------------------------------------------------------------- #
def check_hours(program: Program, report: ValidationReport) -> None:
    kinds = ("lectures", "practice", "labs", "self_study")

    for mi, module in enumerate(program.modules):
        if not module.topics:
            report.add(
                "HOURS_NO_TOPICS",
                Severity.WARNING,
                f"Модуль «{module.title}» без тем — часы нечем подтвердить",
                path=f"modules[{mi}]",
            )
            continue
        topics_sum = Hours.sum([t.hours for t in module.topics])
        for kind in kinds:
            got = getattr(topics_sum, kind)
            want = getattr(module.hours, kind)
            if abs(got - want) > EPS:
                report.add(
                    "HOURS_TOPIC_SUM",
                    Severity.ERROR,
                    f"Модуль {module.index} «{module.title}»: сумма тем по «{kind}» "
                    f"= {got} ч, в строке модуля {want} ч",
                    path=f"modules[{mi}].hours.{kind}",
                    hint="Пересобрать часы тем солвером (core.hours.rebalance_topics)",
                )

    for mi, module in enumerate(program.modules):
        if abs(module.hours.total - sum(
            getattr(module.hours, k) for k in kinds
        )) > EPS:
            report.add(
                "HOURS_ROW_SUM",
                Severity.ERROR,
                f"Модуль {module.index}: ЛК+ПР+ЛБ+СРС не равно трудоёмкости строки",
                path=f"modules[{mi}].hours",
            )

    grand = program.grand_total
    if abs(grand - program.meta.total_hours) > EPS:
        report.add(
            "HOURS_GRAND_TOTAL",
            Severity.ERROR,
            f"Итого {grand} ч (модули {program.modules_hours.total} + "
            f"аттестация {program.final_attestation.hours}) ≠ запрошенных "
            f"{program.meta.total_hours} ч",
            path="meta.total_hours",
        )

    for mi, module in enumerate(program.modules):
        for ti, topic in enumerate(module.topics):
            for kind in kinds:
                val = getattr(topic.hours, kind)
                if val < 0:
                    report.add(
                        "HOURS_NEGATIVE",
                        Severity.ERROR,
                        f"Отрицательные часы в теме {topic.code}",
                        path=f"modules[{mi}].topics[{ti}].hours.{kind}",
                    )
                if abs(val / HOUR_STEP - round(val / HOUR_STEP)) > EPS:
                    report.add(
                        "HOURS_GRAIN",
                        Severity.WARNING,
                        f"Тема {topic.code}: {val} ч не кратно {HOUR_STEP} ч",
                        path=f"modules[{mi}].topics[{ti}].hours.{kind}",
                    )

    if program.final_attestation.hours <= 0:
        report.add(
            "ATTESTATION_HOURS",
            Severity.ERROR,
            "Итоговой аттестации не выделены часы",
            path="final_attestation.hours",
        )


# --------------------------------------------------------------------------- #
# 2. Покрытие результатов обучения
# --------------------------------------------------------------------------- #
MIN_ASSESSMENTS_PER_OUTCOME = 2


def check_coverage(program: Program, report: ValidationReport) -> None:
    if not program.outcomes:
        report.add(
            "COVERAGE_NO_OUTCOMES",
            Severity.ERROR,
            "Не заданы результаты обучения",
            path="outcomes",
        )
        return

    topics_by_outcome: dict[str, list[str]] = {o.id: [] for o in program.outcomes}
    for topic in program.all_topics():
        for oid in topic.outcome_ids:
            topics_by_outcome.setdefault(oid, []).append(topic.code)

    assess_by_outcome: dict[str, set[str]] = {o.id: set() for o in program.outcomes}
    for q in program.test_questions:
        for oid in q.outcome_ids:
            assess_by_outcome.setdefault(oid, set()).add(AssessmentKind.TEST.value)
    for t in program.seminar_tasks:
        for oid in t.outcome_ids:
            assess_by_outcome.setdefault(oid, set()).add(AssessmentKind.SEMINAR.value)
    # Итоговая аттестация покрывает все РО, если так заявлено
    if program.final_attestation.form:
        for oid in assess_by_outcome:
            assess_by_outcome[oid].add("final")

    known = {o.id for o in program.outcomes}
    for oid, codes in topics_by_outcome.items():
        if oid not in known:
            report.add(
                "COVERAGE_UNKNOWN_OUTCOME",
                Severity.ERROR,
                f"Темы ссылаются на неизвестный результат обучения {oid}",
            )
            continue
        if not codes:
            report.add(
                "COVERAGE_OUTCOME_NO_TOPIC",
                Severity.ERROR,
                f"Результат обучения {oid} не раскрыт ни в одной теме",
                path="outcomes",
                hint="Привязать РО к теме или удалить РО",
            )

    for oid, kinds in assess_by_outcome.items():
        if len(kinds) < MIN_ASSESSMENTS_PER_OUTCOME:
            report.add(
                "COVERAGE_OUTCOME_ASSESSMENT",
                Severity.WARNING if not program.test_questions else Severity.ERROR,
                f"Результат обучения {oid} проверяется менее чем "
                f"{MIN_ASSESSMENTS_PER_OUTCOME} оценочными средствами "
                f"(сейчас: {', '.join(sorted(kinds)) or 'нет'})",
                path="test_questions/seminar_tasks",
            )

    for topic in program.all_topics():
        if not topic.outcome_ids:
            report.add(
                "COVERAGE_TOPIC_NO_OUTCOME",
                Severity.WARNING,
                f"Тема {topic.code} «{topic.title}» не привязана ни к одному РО",
            )


# --------------------------------------------------------------------------- #
# 3. Достоверность источников и нормативки
# --------------------------------------------------------------------------- #
def check_sources(program: Program, report: ValidationReport) -> None:
    for ps in program.prof_standards:
        if ps.verification is VerificationStatus.REJECTED:
            report.add(
                "SOURCE_REJECTED",
                Severity.ERROR,
                f"Профстандарт {ps.code} не найден в реестре — удалить из программы",
                path="prof_standards",
            )
        elif ps.verification is VerificationStatus.UNVERIFIED:
            report.add(
                "SOURCE_UNVERIFIED",
                Severity.WARNING,
                f"Профстандарт {ps.code} «{ps.title}» не подтверждён реестром — "
                "в документе печатается с пометкой «требует проверки»",
                path="prof_standards",
            )
    for act in program.legal_acts:
        if act.verification is not VerificationStatus.VERIFIED:
            report.add(
                "LEGAL_UNVERIFIED",
                Severity.WARNING,
                f"Нормативный акт «{act.title}» не подтверждён реестром",
                path="legal_acts",
            )
    unverified = [s for s in program.sources if s.verification is not VerificationStatus.VERIFIED]
    if unverified:
        report.add(
            "SOURCES_UNVERIFIED_LIST",
            Severity.WARNING,
            f"{len(unverified)} из {len(program.sources)} источников требуют "
            "проверки методистом (помечены в документе)",
            path="sources",
        )


# --------------------------------------------------------------------------- #
# 4. Полнота шаблона и требования типа программы
# --------------------------------------------------------------------------- #
REQUIRED_COMMON = {
    "meta.title": lambda p: bool(p.meta.title),
    "goal": lambda p: bool(p.goal),
    "outcomes": lambda p: bool(p.outcomes),
    "modules": lambda p: bool(p.modules),
    "final_attestation.form": lambda p: bool(p.final_attestation.form),
    "meta.audience": lambda p: bool(p.meta.audience),
    "entry_requirements": lambda p: bool(p.entry_requirements),
}

REQUIRED_PK = {
    "annotation": lambda p: bool(p.annotation),
    "prof_standards": lambda p: bool(p.prof_standards),
    "competencies": lambda p: bool(p.competencies),
    "meta.issued_document": lambda p: bool(p.meta.issued_document),
}

REQUIRED_OBSH = {
    "relevance": lambda p: bool(p.relevance),
    "meta.level": lambda p: bool(p.meta.level),
}


def check_template(program: Program, report: ValidationReport) -> None:
    checks = dict(REQUIRED_COMMON)
    checks.update(
        REQUIRED_PK if program.meta.program_type is ProgramType.DPP_PK else REQUIRED_OBSH
    )
    for path, probe in checks.items():
        if not probe(program):
            report.add(
                "TEMPLATE_MISSING",
                Severity.ERROR,
                f"Обязательный раздел шаблона не заполнен: {path}",
                path=path,
            )

    if program.meta.program_type is ProgramType.DOP_OBSH and program.prof_standards:
        report.add(
            "TEMPLATE_OBSH_PROFSTANDARD",
            Severity.WARNING,
            "Общеразвивающая программа не требует привязки к профстандартам — "
            "раздел лишний",
            path="prof_standards",
        )

    # Реквизиты, которые агент обязан оставить человеку
    if program.meta.order_number or program.meta.order_date:
        report.add(
            "PLACEHOLDER_FILLED",
            Severity.ERROR,
            "Реквизиты приказа должны остаться пустыми полями для человека",
            path="meta.order_number",
        )


# --------------------------------------------------------------------------- #
# 5. Согласованность расписания
# --------------------------------------------------------------------------- #
def check_schedule(program: Program, report: ValidationReport) -> None:
    s = program.schedule
    # Аудиторная нагрузка расписания = контактные часы модулей + итоговая аттестация
    # (она тоже проходит на занятии).
    classroom = round(program.modules_hours.contact + program.final_attestation.hours, 1)
    if s.sessions and s.hours_per_session:
        planned = round(s.sessions * s.hours_per_session, 1)
        if classroom and abs(planned - classroom) > EPS:
            report.add(
                "SCHEDULE_SESSIONS",
                Severity.WARNING,
                f"Календарный график: {s.sessions} занятий × {s.hours_per_session} ч "
                f"= {planned} ч, аудиторная нагрузка плана {classroom} ч "
                f"(включая итоговую аттестацию)",
                path="schedule",
            )
    if s.max_hours_per_day and s.days:
        capacity = s.max_hours_per_day * s.days
        if program.meta.total_hours - capacity > EPS:
            report.add(
                "SCHEDULE_CAPACITY",
                Severity.ERROR,
                f"{program.meta.total_hours} ч не укладываются в {s.days} дней "
                f"по {s.max_hours_per_day} ч/день (ёмкость {capacity} ч)",
                path="schedule",
            )


# --------------------------------------------------------------------------- #
# Точка входа
# --------------------------------------------------------------------------- #
CHECKS = (check_hours, check_coverage, check_sources, check_template, check_schedule)


def validate_program(program: Program, *, stage: str = "full") -> ValidationReport:
    """stage='skeleton' — не требует оценочных средств, они ещё не сгенерированы."""
    report = ValidationReport()
    for check in CHECKS:
        if stage == "skeleton" and check is check_coverage:
            check_coverage_skeleton(program, report)
            continue
        check(program, report)
    return report


def check_coverage_skeleton(program: Program, report: ValidationReport) -> None:
    """На этапе каркаса проверяем только связь РО ↔ темы."""
    if not program.outcomes:
        report.add("COVERAGE_NO_OUTCOMES", Severity.ERROR, "Не заданы РО", path="outcomes")
        return
    covered = {oid for t in program.all_topics() for oid in t.outcome_ids}
    for o in program.outcomes:
        if o.id not in covered:
            report.add(
                "COVERAGE_OUTCOME_NO_TOPIC",
                Severity.ERROR,
                f"Результат обучения {o.id} не раскрыт ни в одной теме: «{o.text[:60]}»",
                path="outcomes",
            )
    for t in program.all_topics():
        if not t.outcome_ids:
            report.add(
                "COVERAGE_TOPIC_NO_OUTCOME",
                Severity.WARNING,
                f"Тема {t.code} «{t.title}» не привязана ни к одному РО",
            )
