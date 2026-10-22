"""Модель пишет "null" строкой вместо числа.

Живой случай 23 октября на GigaChat: «Программа по Питону на 200 часов для
студентов» → «could not convert string to float: 'null'», и запрос падал с
подписью «модель недоступна», хотя модель ответила.
"""
from __future__ import annotations

import json

import pytest

from app.llm.base import Completion, LLMProvider, as_number, extract_json
from app.pipeline.intake import run_intake


@pytest.mark.parametrize(
    ("raw", "want"),
    [(200, 200.0), ("200", 200.0), ("200 часов", 200.0), ("0,8", 0.8),
     ("null", None), (None, None), ("", None), (True, None)],
)
def test_as_number(raw: object, want: float | None) -> None:
    assert as_number(raw) == want


def test_null_words_become_none() -> None:
    data = extract_json('{"total_hours": "null", "audience": "None", "items": ["null", "a"]}')
    assert data == {"total_hours": None, "audience": None, "items": [None, "a"]}


class NullishProvider(LLMProvider):
    name = "nullish"
    model = "m"

    async def complete(self, **kwargs) -> Completion:  # noqa: ANN003
        return Completion(text=json.dumps({
            "program_type": "dpp_pk",
            "program_type_confidence": "null",
            "total_hours": "null",
            "audience": "null",
            "entry_level": "None",
            "study_form": "null",
            "attestation_form": "null",
            "max_hours_per_day": "null",
            "min_practice_share": "null",
        }))


@pytest.mark.anyio
async def test_intake_survives_null_strings() -> None:
    result, _ = await run_intake(NullishProvider(), "Программа по Питону на 200 часов для студентов")
    request = result.request
    assert request.total_hours == 200  # объём взят из текста регуляркой
    assert request.audience != "null"
    fields = {q.field for q in result.questions}
    assert "entry_level" in fields, "строка 'None' не должна сойти за ответ"
