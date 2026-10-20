"""Связи компетенция ↔ результат обучения ↔ оценочное средство.

Разбор прогона 20 октября: «мелкие недостатки в покрытии сложных сценариев
связей компетенций и оценочных средств». Здесь эти сценарии: ссылки в
никуда, расхождение сторон связи, компетенция без РО, компетенция, которую
проверяет только итоговая аттестация, — и то, что каркас и наполнение чинят
их сами, без модели.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.validate import validate_program
from app.main import app
from app.pipeline.content import link_outcomes_to_assessments
from app.pipeline.skeleton import link_competencies
from app.schemas.ir import (
    Competency,
    Hours,
    Module,
    Outcome,
    OutcomeKind,
    Program,
    ProgramMeta,
    ProgramType,
    ProfStandard,
    SeminarTask,
    TestQuestion,
    Topic,
)

client = TestClient(app)


def _program(**overrides) -> Program:  # noqa: ANN003
    outcomes = [
        Outcome(id="RO-1", kind=OutcomeKind.KNOWLEDGE, text="знает"),
        Outcome(id="RO-2", kind=OutcomeKind.SKILL, text="умеет"),
        Outcome(id="RO-3", kind=OutcomeKind.ABILITY, text="владеет"),
    ]
    topics = [
        Topic(id="T-1.1", code="1.1", title="а", hours=Hours(lectures=2, practice=2), outcome_ids=["RO-1"]),
        Topic(id="T-1.2", code="1.2", title="б", hours=Hours(lectures=2, practice=2), outcome_ids=["RO-2", "RO-3"]),
    ]
    data = dict(
        meta=ProgramMeta(title="п", program_type=ProgramType.DPP_PK, total_hours=8),
        competencies=[
            Competency(id="PK-1", title="первая", outcome_ids=["RO-1", "RO-2"]),
            Competency(id="PK-2", title="вторая", outcome_ids=["RO-3"]),
        ],
        outcomes=outcomes,
        modules=[Module(id="M-1", index=1, title="м", hours=Hours(lectures=4, practice=4), topics=topics)],
    )
    data.update(overrides)
    return Program(**data)


def _codes(program: Program, stage: str = "skeleton") -> list[str]:
    return [f.code for f in validate_program(program, stage=stage).findings]


def test_clean_map_has_no_link_findings() -> None:
    codes = _codes(_program())
    assert not [c for c in codes if c.startswith(("COMPETENCY_", "OUTCOME_", "ASSESSMENT_"))]


def test_competency_pointing_nowhere() -> None:
    program = _program()
    program.competencies[1].outcome_ids = ["RO-99"]
    codes = _codes(program)
    assert "COMPETENCY_UNKNOWN_OUTCOME" in codes
    assert "COMPETENCY_NO_OUTCOMES" in codes
    assert "OUTCOME_NO_COMPETENCY" in codes  # RO-3 остался без хозяина


def test_two_sides_disagree() -> None:
    program = _program()
    program.outcomes[0].competency_id = "PK-2"
    assert "COMPETENCY_LINK_MISMATCH" in _codes(program)
    program.outcomes[0].competency_id = "PK-77"
    assert "OUTCOME_UNKNOWN_COMPETENCY" in _codes(program)


def test_competency_on_removed_standard() -> None:
    program = _program()
    program.competencies[0].prof_standard_code = "06.001"
    assert "COMPETENCY_UNKNOWN_STANDARD" in _codes(program)
    program.prof_standards = [ProfStandard(code="06.001", title="Программист")]
    assert "COMPETENCY_UNKNOWN_STANDARD" not in _codes(program)


def test_competency_checked_only_by_final_attestation() -> None:
    program = _program(
        test_questions=[
            TestQuestion(id="Q-1", topic_id="T-1.1", outcome_ids=["RO-1"], stem="?", options=["a", "b"], correct=[0]),
        ],
        seminar_tasks=[SeminarTask(id="S-1", topic_id="T-1.2", outcome_ids=["RO-2"], title="з", statement="задание")],
    )
    program.final_attestation.form = "защита проекта"
    codes = _codes(program, stage="full")
    assert "COMPETENCY_NOT_ASSESSED" in codes  # PK-2: только RO-3, его никто не проверяет


def test_assessment_pointing_nowhere_does_not_fake_coverage() -> None:
    program = _program(
        test_questions=[
            TestQuestion(id="Q-1", topic_id="T-9.9", outcome_ids=["RO-404"], stem="?", options=["a", "b"], correct=[0]),
            TestQuestion(id="Q-2", topic_id="T-1.1", outcome_ids=[], stem="?", options=["a", "b"], correct=[0]),
        ],
    )
    report = validate_program(program, stage="full")
    codes = [f.code for f in report.findings]
    assert "ASSESSMENT_UNKNOWN_OUTCOME" in codes
    assert "ASSESSMENT_UNKNOWN_TOPIC" in codes
    assert "ASSESSMENT_NO_OUTCOME" in codes
    # Призрачный RO-404 не должен попадать в отчёт о покрытии реальных РО.
    assert not any("RO-404" in f.message for f in report.findings if f.code == "COVERAGE_OUTCOME_ASSESSMENT")


def test_skeleton_repairs_competency_map() -> None:
    program = _program()
    program.competencies[0].outcome_ids = ["RO-1", "RO-404", "RO-1"]
    program.competencies[1].outcome_ids = []
    program.competencies.append(Competency(id="PK-3", title="третья"))
    assert link_competencies(program) > 0
    codes = _codes(program)
    assert not [c for c in codes if c.startswith(("COMPETENCY_", "OUTCOME_"))], codes
    assert all(o.competency_id for o in program.outcomes)


def test_content_repairs_assessment_links() -> None:
    program = _program(
        test_questions=[
            TestQuestion(id="Q-1", topic_id="T-1.1", outcome_ids=["RO-404"], stem="?", options=["a", "b"], correct=[0]),
            TestQuestion(id="Q-2", topic_id="T-1.2", outcome_ids=[], stem="?", options=["a", "b"], correct=[0]),
        ],
        seminar_tasks=[
            SeminarTask(id="S-1", topic_id="T-1.1", title="з", statement="задание"),
            SeminarTask(id="S-2", topic_id="T-1.2", title="з", statement="задание"),
        ],
    )
    program.final_attestation.form = "защита проекта"
    link_outcomes_to_assessments(program)
    codes = _codes(program, stage="full")
    assert "ASSESSMENT_UNKNOWN_OUTCOME" not in codes
    assert "ASSESSMENT_NO_OUTCOME" not in codes
    assert "COMPETENCY_NOT_ASSESSED" not in codes
    assert "COVERAGE_OUTCOME_ASSESSMENT" not in codes


def test_generated_pk_program_has_full_trace() -> None:
    body = client.post(
        "/api/generate",
        json={"text": "программа повышения квалификации на 72 часа по анализу данных на Python "
                      "для сотрудников банков, очно-заочно, защита проекта"},
    ).json()
    assert body["status"] == "ready"
    codes = [f["code"] for f in body["report"]["findings"]]
    assert not [c for c in codes if c.startswith(("COMPETENCY_", "OUTCOME_", "ASSESSMENT_"))], codes
    program = body["program"]
    assert program["competencies"]
    assert all(o["competency_id"] for o in program["outcomes"])
