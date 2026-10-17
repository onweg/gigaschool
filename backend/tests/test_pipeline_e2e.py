"""Сквозные прогоны пайплайна на офлайн-провайдере.

Проверяется каркас по запросам из кейса: выбор шаблона, точные часы,
ограничения формы обучения и структура стажировки без аудиторных часов.
"""
from __future__ import annotations

import pytest

from app.core.validate import validate_program
from app.llm.mock import MockProvider
from app.pipeline.intake import apply_answers, run_intake
from app.pipeline.skeleton import build_skeleton
from app.render.docx_acr import render_docx
from app.render.lms import render_gift
from app.schemas.ir import ProgramType

REQUEST_1 = (
    "Нужна дополнительная общеразвивающая программа «Первые шаги в Python» для "
    "школьников 8 и 9 классов без опыта программирования. Объем 16 часов, занятия "
    "очно по 2 часа два раза в неделю. Практики должно быть не меньше половины всех "
    "часов. В конце каждый ученик защищает небольшой проект."
)

REQUEST_4 = (
    "Сделайте курс по кибербезопасности для студентов колледжа, примерно на месяц, "
    "чтобы после него ребята могли пойти работать в эту сферу."
)

REQUEST_5 = (
    "Нужна программа повышения квалификации в форме стажировки «DevOps-инженер от "
    "Linux до CI/CD» на 256 часов. Вся работа самостоятельная под руководством "
    "наставника, аудиторных занятий нет. Итоговая аттестация занимает 2 часа. "
    "Срок около двух месяцев, не больше 4 часов в день."
)


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider()


async def test_request_1_obsh_16_hours(provider: MockProvider) -> None:
    intake, _ = await run_intake(provider, REQUEST_1)
    request = intake.request
    apply_answers(
        request,
        {
            "study_form": "очная",
            "audience": "школьники 8-9 классов",
            "entry_level": "без опыта программирования",
            "attestation_form": "защита проекта",
        },
    )
    assert request.ready()
    assert request.program_type is ProgramType.DOP_OBSH
    assert request.total_hours == 16
    assert request.min_practice_share == 0.5
    assert (request.sessions, request.hours_per_session, request.weeks) == (8, 2.0, 4)

    program, _ = await build_skeleton(provider, request)
    report = validate_program(program, stage="skeleton")
    assert report.ok, [f.message for f in report.errors]
    assert program.grand_total == 16
    # Практики не меньше половины объёма — пункт эталонного чек-листа.
    practice = sum(m.hours.practice + m.hours.labs for m in program.modules)
    assert practice >= 8
    # Занятия очные: часов на самостоятельную работу не остаётся.
    assert sum(m.hours.self_study for m in program.modules) == 0
    # Общеразвивающая программа не привязывается к профстандартам.
    assert program.prof_standards == []
    assert render_docx(program)


async def test_request_4_asks_questions_before_building(provider: MockProvider) -> None:
    intake, _ = await run_intake(provider, REQUEST_4)
    assert not intake.request.ready()
    assert 1 <= len(intake.questions) <= 5
    assert "total_hours" in {q.field for q in intake.questions}

    apply_answers(
        intake.request,
        {
            "program_type": "повышение квалификации",
            "total_hours": "72 часа",
            "audience": "студенты 3 курса",
            "entry_level": "основы сетей и Linux",
            "study_form": "очная",
            "attestation_form": "экзамен с практическим заданием",
        },
    )
    assert intake.request.ready()
    program, _ = await build_skeleton(provider, intake.request)
    assert program.meta.program_type is ProgramType.DPP_PK
    assert program.grand_total == 72
    assert program.meta.issued_document == "удостоверение о повышении квалификации"
    assert validate_program(program, stage="skeleton").ok


async def test_request_5_internship_without_contact_hours(provider: MockProvider) -> None:
    intake, _ = await run_intake(provider, REQUEST_5)
    request = intake.request
    # Флаг не выставляется руками: «аудиторных занятий нет» обязан распознаться
    # из текста запроса, иначе часы разъедутся по видам работы.
    assert request.no_contact_hours, "признак «без аудиторных занятий» не извлечён из запроса"
    request.attestation_hours = 2
    apply_answers(
        request,
        {
            "program_type": "повышение квалификации",
            "total_hours": "256 часов",
            "audience": "слушатели стажировки в ИТ-компаниях",
            "entry_level": "базовые знания Linux",
            "study_form": "заочная",
            "attestation_form": "защита отчёта о стажировке",
        },
    )
    program, _ = await build_skeleton(provider, request)
    report = validate_program(program, stage="skeleton")
    assert report.ok, [f.message for f in report.errors]
    assert program.grand_total == 256
    assert program.final_attestation.hours == 2
    assert sum(m.hours.self_study for m in program.modules) == 254
    # Лекции, практики и лабораторные равны нулю во всех строках.
    assert all(m.hours.contact == 0 for m in program.modules)
    assert all(t.hours.contact == 0 for t in program.all_topics())
    assert request.max_hours_per_day == 4


async def test_gift_export_is_importable_shape(provider: MockProvider) -> None:
    from app.pipeline.content import generate_tests

    intake, _ = await run_intake(provider, REQUEST_1)
    apply_answers(
        intake.request,
        {
            "study_form": "очная",
            "audience": "школьники",
            "entry_level": "нет опыта",
            "attestation_form": "защита проекта",
        },
    )
    program, _ = await build_skeleton(provider, intake.request)
    await generate_tests(provider, program, program.all_topics(), total=10)
    gift = render_gift(program)
    assert "$CATEGORY:" in gift
    assert gift.count("=") >= len(program.test_questions)
    assert all(q.outcome_ids for q in program.test_questions)
