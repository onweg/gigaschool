"""Что агент извлекает сам, когда модель не помогает.

Так ловятся регрессии вида «GigaChat не вернул study_form, и каркас не
собрался» — заранее и бесплатно, без обращения к модели и без траты бюджета
площадки. Провайдер здесь возвращает пустой, но валидный JSON: худший
реалистичный случай, когда модель формально ответила, а полезного не дала.

Правило, которое фиксируют тесты: то, что названо в запросе прямым текстом,
агент обязан извлечь без модели. Чего в запросе нет — честно спрашивает
у методиста, а не выдумывает.
"""
from __future__ import annotations

import pytest

from app.llm.base import Completion, LLMProvider, Usage
from app.pipeline.intake import MAX_QUESTIONS, run_intake
from app.schemas.ir import ProgramType, StudyForm


class LazyProvider(LLMProvider):
    """Модель ответила валидным JSON, но пустым."""

    name = "lazy"
    model = "lazy"

    async def complete(self, **kwargs) -> Completion:  # noqa: ANN003
        return Completion(text="{}", usage=Usage(), model="lazy")


#: Тексты из data/acr/case-open-requests.docx, сокращённые до фактов.
REQUESTS = {
    1: "Нужна дополнительная общеразвивающая программа «Первые шаги в Python» для "
       "школьников 8 и 9 классов без опыта программирования. Объем 16 часов, "
       "занятия очно по 2 часа два раза в неделю.",
    2: "Программа повышения квалификации «Искусственный интеллект в работе учителя» "
       "для учителей школ Ингушетии, 36 часов, очно-заочно с применением "
       "дистанционных технологий.",
    3: "Нужна программа повышения квалификации на 72 часа «Анализ данных на Python» "
       "для сотрудников банков. Формат очно-заочный, срок 4 недели.",
    4: "Сделайте курс по кибербезопасности для студентов колледжа, примерно на месяц.",
    5: "Программа повышения квалификации в форме стажировки на 256 часов. Вся работа "
       "самостоятельная, аудиторных занятий нет. Срок около двух месяцев.",
}

#: Что обязано извлечься из каждого запроса без всякой помощи модели.
EXPECTED = {
    1: {"program_type": ProgramType.DOP_OBSH, "total_hours": 16, "study_form": StudyForm.FULL_TIME},
    2: {"program_type": ProgramType.DPP_PK, "total_hours": 36, "study_form": StudyForm.PART_TIME},
    3: {"program_type": ProgramType.DPP_PK, "total_hours": 72, "study_form": StudyForm.PART_TIME},
    5: {"program_type": ProgramType.DPP_PK, "total_hours": 256, "study_form": StudyForm.DISTANT},
}


@pytest.fixture
def lazy() -> LazyProvider:
    return LazyProvider()


@pytest.mark.anyio
@pytest.mark.parametrize("num", sorted(EXPECTED))
async def test_facts_named_in_request_are_extracted_without_model(num: int, lazy: LazyProvider) -> None:
    intake, _ = await run_intake(lazy, REQUESTS[num])
    request = intake.request

    for field, expected in EXPECTED[num].items():
        actual = getattr(request, field)
        assert actual == expected, f"запрос {num}: {field} = {actual}, ожидалось {expected}"


@pytest.mark.anyio
async def test_vague_request_still_asks(lazy: LazyProvider) -> None:
    """Запрос 4: ни типа, ни объёма — агент обязан спросить, а не домыслить."""
    intake, _ = await run_intake(lazy, REQUESTS[4])

    asked = [q.field for q in intake.questions]
    assert "program_type" in asked
    assert "total_hours" in asked
    assert intake.request.total_hours is None, "объём не должен появиться из воздуха"


@pytest.mark.anyio
@pytest.mark.parametrize("num", sorted(REQUESTS))
async def test_question_limit_holds(num: int, lazy: LazyProvider) -> None:
    """Чек-лист кейса: вопросов не больше пяти даже в худшем случае."""
    intake, _ = await run_intake(lazy, REQUESTS[num])

    assert len(intake.questions) <= MAX_QUESTIONS


@pytest.mark.anyio
async def test_lesson_length_is_not_mistaken_for_volume(lazy: LazyProvider) -> None:
    """«по 2 часа» и «не больше 4 часов в день» — это нагрузка, не объём."""
    intake, _ = await run_intake(
        lazy,
        "Занятия по 2 часа два раза в неделю, не больше 4 часов в день. "
        "Общий объем 72 часа.",
    )

    assert intake.request.total_hours == 72


@pytest.mark.anyio
async def test_model_answer_wins_over_text_when_present() -> None:
    """Разбор текста — подстраховка, а не замена: сказанное моделью в приоритете."""

    class PreciseProvider(LLMProvider):
        name = "precise"
        model = "precise"

        async def complete(self, **kwargs) -> Completion:  # noqa: ANN003
            return Completion(
                text='{"total_hours": 144, "program_type": "dpp_pk", "study_form": "zaochnaya"}',
                usage=Usage(),
                model="precise",
            )

    intake, _ = await run_intake(PreciseProvider(), REQUESTS[3])

    assert intake.request.total_hours == 144
    assert intake.request.study_form is StudyForm.DISTANT
