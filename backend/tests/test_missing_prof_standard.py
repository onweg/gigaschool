"""ДПП ПК без подходящего профстандарта не должна заходить в тупик.

Живой случай 23 октября: запрос «ИТ-направление, 200 часов» не совпал ни с
одним словом реестра, автопроверка выдала блокирующую TEMPLATE_MISSING
по prof_standards, и кнопка «Утвердить каркас» осталась неактивной без
способа это исправить.
"""
from __future__ import annotations

from app.core.validate import Severity, validate_program
from app.pipeline.skeleton import _standards_from_generated_text
from app.schemas.ir import Competency, Outcome, OutcomeKind, Program, ProgramMeta, ProgramType


def _pk(goal: str) -> Program:
    return Program(
        meta=ProgramMeta(title="ИТ-направление", program_type=ProgramType.DPP_PK, total_hours=200),
        goal=goal,
        competencies=[Competency(id="PK-1", title="Разработка программ", outcome_ids=["RO-1"])],
        outcomes=[Outcome(id="RO-1", kind=OutcomeKind.SKILL, text="умеет писать код на Python")],
    )


def test_standard_is_found_from_generated_text() -> None:
    program = _pk("Освоение базовых навыков программирования на Python")
    _standards_from_generated_text(program)
    assert [s.code for s in program.prof_standards] and program.prof_standards[0].code == "06.001"
    assert program.competencies[0].prof_standard_code == "06.001"


def test_missing_standard_warns_but_does_not_block() -> None:
    program = _pk("Освоение чего-то, чего нет в реестре")
    program.outcomes[0].text = "умеет что-то"
    program.competencies[0].title = "что-то"
    _standards_from_generated_text(program)
    assert program.prof_standards == []
    findings = [
        f for f in validate_program(program, stage="skeleton").findings
        if f.code == "TEMPLATE_MISSING" and f.path == "prof_standards"
    ]
    assert findings and all(f.severity is Severity.WARNING for f in findings)


def test_other_template_sections_still_block() -> None:
    program = _pk("цель")
    program.competencies = []
    errors = [f for f in validate_program(program, stage="skeleton").findings
              if f.code == "TEMPLATE_MISSING" and f.severity is Severity.ERROR]
    assert any(f.path == "competencies" for f in errors)
