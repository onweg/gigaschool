"""Сверка часов, встроенная в структуру данных.

Разбор прогона 20 октября: «в некоторых структурах отсутствует встроенная
проверка суммарных часов относительно общего объёма». Теперь сверка живёт в
самой модели: отрицательные часы она не принимает, строка модуля сверяется
с темами, итог — с заказанным объёмом, и результат едет в каждой выгрузке.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import READY
from pydantic import ValidationError

from app.main import app
from app.schemas.ir import Hours, Module, Topic

client = TestClient(app)

REQUEST = (
    "программа повышения квалификации на 72 часа по анализу данных на Python "
    "для сотрудников банков, очно-заочно, защита проекта"
)


def _ready() -> tuple[str, dict]:
    body = client.post("/api/generate", json={"text": REQUEST, "answers": READY}).json()
    assert body["status"] == "ready", body
    return body["session_id"], body["program"]


def test_hours_refuse_negative_on_create_and_assign() -> None:
    with pytest.raises(ValidationError):
        Hours(lectures=-1)
    hours = Hours(lectures=2)
    with pytest.raises(ValidationError):
        hours.practice = -0.5


def test_module_reports_gap_with_topics() -> None:
    module = Module(
        id="M-1", index=1, title="м",
        hours=Hours(lectures=4, practice=4),
        topics=[Topic(id="T-1.1", code="1.1", title="т", hours=Hours(lectures=4, practice=2))],
    )
    assert module.hours_gaps() == {"practice": (2.0, 4.0)}
    module.topics[0].hours.practice = 4
    assert module.hours_gaps() == {}


def test_generated_program_carries_its_own_hours_check() -> None:
    _, program = _ready()
    check = program["hours_check"]
    assert check["ok"] is True
    assert check["total"] == check["declared"] == 72
    assert check["modules"] + check["attestation"] == check["total"]
    assert check["modules_off"] == []


def test_hours_check_is_in_json_export_and_reacts_to_breakage() -> None:
    sid, program = _ready()
    topic = program["modules"][0]["topics"][0]
    new = topic["hours"]["self_study"] + 1
    state = client.post(
        f"/api/sessions/{sid}/skeleton/hours",
        json={"topic_hours": {topic["id"]: {"self_study": new}}},
    ).json()
    check = state["program"]["hours_check"]
    assert check["ok"] is False and check["gap"] == 1
    exported = client.get(f"/api/sessions/{sid}/export/json").json()
    assert exported["program"]["hours_check"] == check


@pytest.mark.parametrize(
    "patch",
    [
        {"topic_hours": {"T-1.1": {"lectures": -2}}},
        {"topic_hours": {"T-1.1": {"total": 10}}},
        {"topic_hours": {"T-1.1": {"practice": 1.3}}},
        {"module_hours": {"M-1": 0}},
    ],
)
def test_broken_patch_is_refused_and_program_untouched(patch: dict) -> None:
    sid, program = _ready()
    response = client.post(f"/api/sessions/{sid}/skeleton/hours", json=patch)
    assert response.status_code == 400, response.text
    after = client.get(f"/api/sessions/{sid}").json()["program"]
    assert after["modules"] == program["modules"]
