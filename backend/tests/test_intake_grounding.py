"""Модель не должна додумывать параметры, которых нет в запросе.

Живой случай 23 октября: «Программа по Питону на 200 часов для студентов» —
модель сама поставила входной уровень и форму аттестации, и агент задал
два уточнения вместо четырёх.
"""
from __future__ import annotations

import json

import pytest

from app.llm.base import Completion, LLMProvider
from app.pipeline.intake import _grounded, run_intake


class GuessingProvider(LLMProvider):
    """Отвечает так, как отвечал GigaChat: додумывает недостающее."""

    name = "guess"
    model = "m"

    def __init__(self, **fields: object) -> None:
        self.fields = fields

    async def complete(self, **kwargs) -> Completion:  # noqa: ANN003
        return Completion(text=json.dumps(self.fields, ensure_ascii=False))


@pytest.mark.anyio
async def test_guessed_fields_become_questions() -> None:
    provider = GuessingProvider(
        program_type="dop_obsh", program_type_confidence=0.5,
        total_hours=200, audience="студенты", entry_level="начальный уровень",
        attestation_form="зачёт",
    )
    result, _ = await run_intake(provider, "Программа по Питону на 200 часов для студентов")
    fields = {q.field for q in result.questions}
    assert {"entry_level", "attestation_form", "program_type", "study_form"} <= fields
    assert "audience" not in fields and "total_hours" not in fields
    assert any("нет в запросе" in n for n in result.notes)


@pytest.mark.anyio
async def test_hours_not_in_text_are_asked() -> None:
    provider = GuessingProvider(program_type="dpp_pk", program_type_confidence=0.9, total_hours=72)
    result, _ = await run_intake(
        provider, "Сделайте курс по кибербезопасности для студентов колледжа, примерно на месяц"
    )
    assert "total_hours" in {q.field for q in result.questions}


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("защита проекта", "В конце каждый ученик защищает небольшой проект"),
        ("работа в Excel", "сотрудников, которые сейчас работают в Excel"),
        ("без опыта программирования", "школьников без опыта программирования"),
        ("зачёт по портфолио", "Итоговая аттестация в форме зачёта по портфолио"),
        ("учителя школ Ингушетии", "для учителей школ Ингушетии"),
    ],
)
def test_real_values_from_case_are_kept(value: str, text: str) -> None:
    assert _grounded(value, text)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("начальный уровень", "Программа по Питону на 200 часов для студентов"),
        ("зачёт", "Программа по Питону на 200 часов для студентов"),
        ("экзамен", "курс по кибербезопасности для студентов колледжа"),
    ],
)
def test_guesses_are_dropped(value: str, text: str) -> None:
    assert not _grounded(value, text)
