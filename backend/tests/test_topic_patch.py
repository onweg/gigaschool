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

from conftest import READY

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
    body = client.post("/api/generate", json={"text": REQUEST, "answers": READY}).json()
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


class RenamingProvider(LLMProvider):
    """Модель, которая по просьбе методиста предлагает новое название темы."""

    name = "renaming"
    model = "renaming"

    def __init__(self) -> None:
        from app.llm.mock import MockProvider

        self.inner = MockProvider()

    async def complete(self, *, system: str, user: str, **kwargs):  # noqa: ANN003, ANN201
        if "SKILL:topic_content" in system:
            return Completion(
                text='{"content": "Оконные функции: OVER, PARTITION BY.", '
                     '"activities": "ПР: практикум", "title": "Оконные функции SQL"}',
                usage=Usage(),
                model=self.name,
            )
        return await self.inner.complete(system=system, user=user, **kwargs)


def test_model_renames_topic_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """«Переименуй тему» словами — без ручной правки поля с названием."""
    session_id, program = _ready_session()
    topic_id = program["modules"][0]["topics"][0]["id"]

    monkeypatch.setattr("app.main.provider", RenamingProvider())
    body = client.post(
        f"/api/sessions/{session_id}/topics/{topic_id}/regenerate",
        json={"instruction": "переименуй тему под оконные функции SQL"},
    ).json()

    topic = next(
        t for m in body["program"]["modules"] for t in m["topics"] if t["id"] == topic_id
    )
    assert topic["title"] == "Оконные функции SQL"


def test_title_is_left_alone_without_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без пожелания название не трогаем, даже если модель его прислала."""
    session_id, program = _ready_session()
    topic = program["modules"][0]["topics"][0]
    original = topic["title"]

    monkeypatch.setattr("app.main.provider", RenamingProvider())
    body = client.post(
        f"/api/sessions/{session_id}/topics/{topic['id']}/regenerate",
        json={"regenerate_tests": False},
    ).json()

    after = next(
        t for m in body["program"]["modules"] for t in m["topics"] if t["id"] == topic["id"]
    )
    assert after["title"] == original


# --- Пожелание до утверждения каркаса (23 октября) ---------------------------
# Раньше пожелание к теме, данное на этапе каркаса, терялось: сборка
# комплекта генерировала все темы заново без него, а тесты одной темы
# ломали проверку покрытия и блокировали «Утвердить каркас».


def _skeleton_session() -> tuple[str, dict]:
    sid = client.post("/api/sessions", json={"text": REQUEST}).json()["id"]
    client.post(f"/api/sessions/{sid}/answers", json={"answers": READY})
    state = client.post(f"/api/sessions/{sid}/skeleton").json()
    assert state["stage"] == "skeleton", state.get("missing")
    return sid, state["program"]


def test_wish_on_skeleton_is_kept_until_kit(monkeypatch: pytest.MonkeyPatch) -> None:
    sid, program = _skeleton_session()
    topic = program["modules"][0]["topics"][0]

    spy = SpyProvider()
    monkeypatch.setattr("app.main.provider", spy)
    wish = "добавь оконные функции SQL"
    state = client.post(
        f"/api/sessions/{sid}/topics/{topic['id']}/regenerate", json={"instruction": wish}
    ).json()

    assert spy.prompts == [], "до утверждения каркаса к модели не ходим"
    saved = state["program"]["modules"][0]["topics"][0]
    assert saved["instruction"] == wish
    assert state["program"]["test_questions"] == []
    assert not [f for f in state["report"]["findings"] if f["severity"] == "error"]

    assert client.post(f"/api/sessions/{sid}/approve").status_code == 200
    client.post(f"/api/sessions/{sid}/content", json={})
    with_wish = [p for p in spy.prompts if wish in p]
    assert with_wish, "пожелание должно дойти до сборки комплекта"
    assert all(topic["title"] in p or topic["code"] in p for p in with_wish)


def test_approve_checks_skeleton_by_skeleton_rules() -> None:
    """Отчёт полного комплекта не должен блокировать утверждение каркаса.

    Живой случай: правка темы на каркасе сгенерировала тесты одной темы, и
    «РО проверяется менее чем двумя средствами» заперло кнопку «Утвердить».
    """
    from app.core.validate import validate_program
    from app.main import store
    from app.schemas.ir import TestQuestion

    sid, program = _skeleton_session()
    session = store.get(sid)
    topic = session.program.modules[0].topics[0]
    session.program.test_questions = [
        TestQuestion(id="Q-1", topic_id=topic.id, outcome_ids=topic.outcome_ids[:1],
                     stem="?", options=["a", "b"], correct=[0])
    ]
    session.report = validate_program(session.program, stage="full")
    assert session.report.errors, "предпосылка: полный отчёт с ошибками покрытия"
    store.save(session)

    response = client.post(f"/api/sessions/{sid}/approve")
    assert response.status_code == 200, response.text
