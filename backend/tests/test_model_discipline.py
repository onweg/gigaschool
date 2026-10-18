"""Код доводит результат до нормы, когда модель не соблюдает требования.

Все проверки здесь выросли из живых прогонов по открытым запросам кейса:
YandexGPT и GigaChat по-разному нарушали количественные требования методиста
(доля практики, число вопросов, покрытие результатов обучения, слайды к первым
занятиям). Ответ один и тот же: считает и доводит код, модель только пишет текст.
"""
from __future__ import annotations

import pytest

from app.core.hours import split_by_kind
from app.core.validate import validate_program
from app.llm.mock import MockProvider
from app.pipeline.content import MIN_SLIDE_TOPICS, build_content, link_outcomes_to_assessments
from app.pipeline.intake import run_intake
from app.pipeline.skeleton import build_skeleton
from app.schemas.ir import SeminarTask, StudyForm, TestQuestion
from app.schemas.request import ProgramRequest
from app.schemas.ir import ProgramType

REQUEST_SCHOOL = (
    "Нужна дополнительная общеразвивающая программа «Первые шаги в Python» для "
    "школьников 8 и 9 классов без опыта программирования. Объем 16 часов, занятия "
    "очно по 2 часа два раза в неделю. Практики должно быть не меньше половины "
    "всех часов. Нужны итоговый тест на 10 вопросов и слайды для первых двух занятий."
)
REQUEST_INTERNSHIP = (
    "Программа повышения квалификации в форме стажировки на 256 часов. Вся работа "
    "самостоятельная под руководством наставника, аудиторных занятий нет."
)


@pytest.fixture
def provider() -> MockProvider:
    return MockProvider()


@pytest.mark.anyio
async def test_practice_share_counts_from_full_volume(provider: MockProvider) -> None:
    """«Половина всех часов» — от объёма программы, а не от часов модулей.

    На 16 ч с 2 ч аттестации наивный расчёт даёт 7 ч практики вместо 8.
    """
    intake, _ = await run_intake(provider, REQUEST_SCHOOL)
    request = intake.request
    request.audience = "школьники 8–9 классов"
    request.entry_level = "без опыта"
    program, _ = await build_skeleton(provider, request)

    practice = program.modules_hours.practice + program.modules_hours.labs
    assert practice >= program.meta.total_hours / 2, (
        f"практики {practice:g} ч при объёме {program.meta.total_hours:g} ч"
    )


@pytest.mark.anyio
async def test_slides_cover_first_lessons_not_first_module(provider: MockProvider) -> None:
    """Слайды нужны к первым занятиям, сколько бы тем ни было в первом модуле."""
    intake, _ = await run_intake(provider, REQUEST_SCHOOL)
    request = intake.request
    request.audience = "школьники"
    request.entry_level = "без опыта"
    program, _ = await build_skeleton(provider, request)
    await build_content(provider, program, test_total=10)

    first = [t.id for t in program.all_topics()[:MIN_SLIDE_TOPICS]]
    covered = {plan.topic_id for plan in program.lecture_plans}
    assert set(first) <= covered, f"нет планов для тем {set(first) - covered}"


@pytest.mark.anyio
async def test_question_quota_is_met(provider: MockProvider) -> None:
    """Сколько вопросов просили, столько и собирается."""
    intake, _ = await run_intake(provider, REQUEST_SCHOOL)
    request = intake.request
    request.audience = "школьники"
    request.entry_level = "без опыта"
    program, _ = await build_skeleton(provider, request)
    await build_content(provider, program, test_total=10)

    assert len(program.test_questions) == 10


@pytest.mark.anyio
async def test_study_form_follows_from_absent_contact_hours(provider: MockProvider) -> None:
    """Нет аудиторных занятий — форма заочная, лишнего вопроса не задаём."""
    intake, _ = await run_intake(provider, REQUEST_INTERNSHIP)

    assert intake.request.no_contact_hours
    assert intake.request.study_form is StudyForm.DISTANT
    assert "study_form" not in [q.field for q in intake.questions]


def test_link_outcomes_repairs_missing_assessment_links() -> None:
    """Забытую моделью привязку РО восстанавливаем без обращения к модели."""
    request = ProgramRequest(
        title="Тест",
        program_type=ProgramType.DPP_PK,
        total_hours=72,
        audience="специалисты",
        entry_level="базовый",
    )
    import asyncio

    program, _ = asyncio.run(build_skeleton(MockProvider(), request))
    topic = program.all_topics()[0]
    outcome_id = topic.outcome_ids[0]

    # Модель вернула средства без привязки к РО — так бывает чаще всего.
    program.test_questions.append(
        TestQuestion(id="Q-1", topic_id=topic.id, outcome_ids=[], stem="?", options=["a", "b"])
    )
    program.seminar_tasks.append(
        SeminarTask(id="SEM-1", topic_id=topic.id, outcome_ids=[], title="з", statement="с")
    )

    fixed = link_outcomes_to_assessments(program)

    assert fixed >= 2, "обе связи должны восстановиться"
    assert outcome_id in program.test_questions[0].outcome_ids
    assert outcome_id in program.seminar_tasks[0].outcome_ids


def test_practice_share_solver_respects_minimum() -> None:
    """Солвер поднимает практику до доли, забирая часы у лекций и СРС."""
    hours = split_by_kind(14, lectures=3, practice=1, self_study=1, min_practice_share=8 / 14)

    assert hours.total == 14
    assert hours.practice + hours.labs >= 8


def test_raw_newlines_inside_json_do_not_break_parsing() -> None:
    """Живой сбой: YandexGPT прислал перевод строки прямо внутри значения.

    По стандарту такой символ должен быть экранирован, и строгий парсер падает
    с «Invalid control character». Ответ при этом осмысленный, терять его из-за
    форматирования нельзя — иначе на защите демо обрывается на ровном месте.
    """
    from app.llm.base import extract_json

    broken = '{"title": "Программа\nна 256 часов", "modules": [{"title": "Модуль\t1"}]}'

    data = extract_json(broken)

    assert data["title"].startswith("Программа")
    assert len(data["modules"]) == 1
    # Текст вокруг JSON тоже не мешает
    assert extract_json("Вот ответ: " + broken + " готово")["title"].startswith("Программа")


def test_garbage_is_still_rejected() -> None:
    """Нестрогий разбор не должен превращаться в «принимаем что угодно»."""
    from app.llm.base import extract_json

    with pytest.raises(ValueError):
        extract_json("совсем не json")
    with pytest.raises(ValueError):
        extract_json("")
