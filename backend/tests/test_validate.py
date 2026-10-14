"""Тесты автопроверки: каждый тест воспроизводит пункт чек-листа кейса."""
from __future__ import annotations

from app.core.validate import Severity, validate_program
from app.registry.legal import acts_for, get_prof_standard
from app.schemas.ir import (
    Competency,
    FinalAttestation,
    Hours,
    Module,
    Outcome,
    OutcomeKind,
    Program,
    ProgramMeta,
    ProgramType,
    Source,
    TestQuestion,
    Topic,
    VerificationStatus,
)


def make_program(**overrides) -> Program:
    outcome = Outcome(id="RO-1", kind=OutcomeKind.SKILL, text="применять инструменты")
    topic = Topic(
        id="T-1.1",
        code="1.1",
        title="Тема",
        hours=Hours(lectures=4, practice=6),
        outcome_ids=["RO-1"],
    )
    module = Module(
        id="M-1",
        index=1,
        title="Модуль",
        hours=Hours(lectures=4, practice=6),
        topics=[topic],
    )
    program = Program(
        meta=ProgramMeta(
            title="Тест",
            program_type=ProgramType.DPP_PK,
            total_hours=12,
            audience="слушатели",
            issued_document="удостоверение о повышении квалификации",
        ),
        annotation="аннотация",
        goal="цель",
        entry_requirements="требования",
        legal_acts=acts_for("dpp_pk"),
        prof_standards=[get_prof_standard("06.015")],
        competencies=[Competency(id="PK-1", title="компетенция", outcome_ids=["RO-1"])],
        outcomes=[outcome],
        modules=[module],
        final_attestation=FinalAttestation(hours=2, form="зачёт"),
    )
    for key, value in overrides.items():
        setattr(program, key, value)
    return program


def codes(report) -> set[str]:
    return {f.code for f in report.findings}


def test_valid_skeleton_passes() -> None:
    report = validate_program(make_program(), stage="skeleton")
    assert report.ok, [f.message for f in report.errors]


def test_topic_sum_mismatch_is_error() -> None:
    program = make_program()
    program.modules[0].topics[0].hours.practice = 5  # тема больше не сходится с модулем
    report = validate_program(program, stage="skeleton")
    assert "HOURS_TOPIC_SUM" in codes(report)
    assert not report.ok


def test_grand_total_mismatch_is_error() -> None:
    program = make_program()
    program.meta.total_hours = 248
    report = validate_program(program, stage="skeleton")
    assert "HOURS_GRAND_TOTAL" in codes(report)


def test_reference_error_not_propagated() -> None:
    """Образец стажировки: ЛК=ПР=ЛБ=0 в строках, но 66 ПР и 6 ЛБ в «Итого».

    Автопроверка обязана поймать такую строку, если она попадёт в программу.
    """
    program = make_program()
    program.meta.total_hours = 248
    program.modules = [
        Module(
            id="M-1",
            index=1,
            title="Раздел",
            hours=Hours(self_study=246),
            topics=[
                Topic(
                    id="T-1.1",
                    code="1.1",
                    title="Тема",
                    hours=Hours(self_study=246),
                    outcome_ids=["RO-1"],
                )
            ],
        )
    ]
    report = validate_program(program, stage="skeleton")
    assert report.ok, [f.message for f in report.errors]
    # А теперь «переносим» ошибку образца: практика появилась из ниоткуда.
    program.modules[0].hours.practice = 66
    broken = validate_program(program, stage="skeleton")
    assert "HOURS_TOPIC_SUM" in codes(broken)


def test_outcome_without_topic_is_error() -> None:
    program = make_program()
    program.outcomes.append(Outcome(id="RO-2", kind=OutcomeKind.KNOWLEDGE, text="знать"))
    report = validate_program(program, stage="skeleton")
    assert "COVERAGE_OUTCOME_NO_TOPIC" in codes(report)


def test_outcome_needs_two_assessments_on_full_stage() -> None:
    program = make_program()
    program.test_questions = [
        TestQuestion(
            id="Q-1",
            topic_id="T-1.1",
            outcome_ids=["RO-1"],
            stem="?",
            options=["a", "b"],
            correct=[0],
        )
    ]
    program.final_attestation.form = ""  # аттестация не описана → покрытие падает
    report = validate_program(program, stage="full")
    assert "COVERAGE_OUTCOME_ASSESSMENT" in codes(report)


def test_filled_order_requisites_is_error() -> None:
    """Реквизиты приказа агент обязан оставить человеку."""
    program = make_program()
    program.meta.order_number = "12-ОБР"
    report = validate_program(program, stage="skeleton")
    assert "PLACEHOLDER_FILLED" in codes(report)


def test_rejected_source_is_error() -> None:
    program = make_program()
    program.prof_standards[0].verification = VerificationStatus.REJECTED
    report = validate_program(program, stage="skeleton")
    assert "SOURCE_REJECTED" in codes(report)


def test_unverified_source_is_warning_not_error() -> None:
    program = make_program()
    program.sources = [Source(id="S-1", citation="Книга без проверки")]
    report = validate_program(program, stage="skeleton")
    assert report.ok
    assert any(f.severity is Severity.WARNING for f in report.findings)


def test_obsh_program_requires_level_not_profstandard() -> None:
    program = make_program()
    program.meta.program_type = ProgramType.DOP_OBSH
    program.relevance = "актуальность"
    program.meta.level = "базовый"
    report = validate_program(program, stage="skeleton")
    assert "TEMPLATE_OBSH_PROFSTANDARD" in codes(report)  # профстандарт тут лишний
    assert report.ok


def test_schedule_capacity_overflow() -> None:
    program = make_program()
    program.schedule.days = 2
    program.schedule.max_hours_per_day = 4
    report = validate_program(program, stage="skeleton")
    assert "SCHEDULE_CAPACITY" in codes(report)
