"""Точечная доработка темы: пожелание методиста должно дойти до модели.

Требование кейса: «Методист может точечно поправить отдельные части, например
попросить заменить тему 2.3 или усложнить тест, и агент пересобирает только
затронутое, сохраняя согласованность остального».

Эндпоинт существовал и раньше, но текст пожелания в промпт не попадал —
тема просто генерировалась заново. Тесты фиксируют, что теперь доходит.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.llm.base import Completion, LLMProvider, Usage
from app.main import app

client = TestClient(app)

REQUEST = (
    "программа повышения квалификации на 72 часа по анализу данных на Python "
    "для сотрудников банков, очно-заочно, защита проекта"
)


class SpyProvider(LLMProvider):
    """Запоминает все промпты, чтобы проверить, что в них попало."""

    name = "spy"
    model = "spy"

    def __init__(self) -> None:
        self.prompts: list[str] = []
        from app.llm.mock import MockProvider

        self.inner = MockProvider()

    async def complete(self, *, system: str, user: str, **kwargs) -> Completion:  # noqa: ANN003
        self.prompts.append(user)
        return await self.inner.complete(system=system, user=user, **kwargs)


def _ready_session() -> tuple[str, dict]:
    body = client.post("/api/generate", json={"text": REQUEST}).json()
    assert body["status"] == "ready", body
    return body["session_id"], body["program"]


def test_instruction_reaches_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    session_id, program = _ready_session()
    topic_id = program["modules"][0]["topics"][0]["id"]

    spy = SpyProvider()
    monkeypatch.setattr("app.main.provider", spy)

    response = client.post(
        f"/api/sessions/{session_id}/topics/{topic_id}/regenerate",
        json={"instruction": "добавь оконные функции SQL и примеры из банков"},
    )

    assert response.status_code == 200, response.text
    assert spy.prompts, "модель должна быть вызвана"
    assert any("оконные функции SQL" in p for p in spy.prompts), (
        "пожелание методиста не дошло до промпта"
    )


def test_title_can_be_replaced() -> None:
    """«Замени тему 2.3» — методист задаёт новое название."""
    session_id, program = _ready_session()
    topic_id = program["modules"][0]["topics"][0]["id"]

    body = client.post(
        f"/api/sessions/{session_id}/topics/{topic_id}/regenerate",
        json={"title": "Оконные функции SQL", "instruction": "упор на практику"},
    ).json()

    topic = next(
        t for m in body["program"]["modules"] for t in m["topics"] if t["id"] == topic_id
    )
    assert topic["title"] == "Оконные функции SQL"


def test_rest_of_the_kit_survives() -> None:
    """Пересобирается только затронутая тема, остальное не трогаем."""
    session_id, program = _ready_session()
    topics = [t for m in program["modules"] for t in m["topics"]]
    target, other = topics[0], topics[1]

    before = client.get(f"/api/sessions/{session_id}").json()["program"]
    other_before = next(
        t for m in before["modules"] for t in m["topics"] if t["id"] == other["id"]
    )

    body = client.post(
        f"/api/sessions/{session_id}/topics/{target['id']}/regenerate",
        json={"instruction": "усложни"},
    ).json()

    after = body["program"]
    other_after = next(
        t for m in after["modules"] for t in m["topics"] if t["id"] == other["id"]
    )
    assert other_after["content"] == other_before["content"], "соседняя тема изменилась"
    assert other_after["hours"] == other_before["hours"], "часы соседней темы поехали"


def test_hours_stay_exact_after_patch() -> None:
    """Доработка темы не должна ломать арифметику всей программы."""
    session_id, program = _ready_session()
    topic_id = program["modules"][0]["topics"][0]["id"]

    body = client.post(
        f"/api/sessions/{session_id}/topics/{topic_id}/regenerate",
        json={"instruction": "сделай сложнее"},
    ).json()

    modules = body["program"]["modules"]
    total = sum(m["hours"]["total"] for m in modules)
    total += body["program"]["final_attestation"]["hours"]
    assert total == 72

    codes = [
        f["code"] for f in body["report"]["findings"]
        if f["severity"] == "error" and f["code"].startswith("HOURS_")
    ]
    assert not codes, codes
