"""Маршрут методиста для неподтверждённых источников.

Разбор прогона 20 октября: источники помечаются, но интерфейс не даёт
методисту быстро их проверить. Теперь у каждого — ссылка на реестр и два
решения: подтвердить или убрать. Тесты фиксируют, что решение доходит до
программы, автопроверки и документа.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import READY

from app.main import app

client = TestClient(app)

REQUEST = (
    "программа повышения квалификации на 72 часа по анализу данных на Python "
    "для сотрудников банков, очно-заочно, защита проекта"
)


def _ready() -> tuple[str, list[dict]]:
    body = client.post("/api/generate", json={"text": REQUEST, "answers": READY}).json()
    assert body["status"] == "ready", body
    sid = body["session_id"]
    items = client.get(f"/api/sessions/{sid}/sources/pending").json()["items"]
    assert items, "заглушка должна давать неподтверждённые источники"
    return sid, items


def _codes(state: dict) -> list[str]:
    return [f["code"] for f in state["report"]["findings"]]


def test_every_pending_item_says_where_to_check() -> None:
    _, items = _ready()
    for item in items:
        assert item["check_url"].startswith("http")
        assert item["what_to_check"]
        assert item["kind"] in ("prof_standard", "legal_act", "source")


def test_confirm_clears_finding() -> None:
    sid, items = _ready()
    state = None
    for item in items:
        response = client.post(
            f"/api/sessions/{sid}/sources/review",
            json={"kind": item["kind"], "key": item["key"], "action": "confirm"},
        )
        assert response.status_code == 200, response.text
        state = response.json()
    assert state["pending_sources"] == []
    codes = _codes(state)
    assert "SOURCE_UNVERIFIED" not in codes
    assert "SOURCES_UNVERIFIED_LIST" not in codes
    std = state["program"]["prof_standards"][0]
    assert std["verification"] == "verified"
    assert "методистом" in std["note"]


def test_remove_prof_standard_clears_competency_links() -> None:
    sid, items = _ready()
    std = next(i for i in items if i["kind"] == "prof_standard")
    state = client.post(
        f"/api/sessions/{sid}/sources/review",
        json={"kind": "prof_standard", "key": std["key"], "action": "remove"},
    ).json()
    program = state["program"]
    assert all(s["code"] != std["key"] for s in program["prof_standards"])
    assert all(c.get("prof_standard_code") != std["key"] for c in program["competencies"])
    assert all(i["key"] != std["key"] for i in state["pending_sources"])


def test_removed_source_leaves_document() -> None:
    sid, items = _ready()
    source = next(i for i in items if i["kind"] == "source")
    client.post(
        f"/api/sessions/{sid}/sources/review",
        json={"kind": "source", "key": source["key"], "action": "remove"},
    )
    exported = client.get(f"/api/sessions/{sid}/export/json").json()
    assert all(s["id"] != source["key"] for s in exported["program"]["sources"])


def test_decision_is_logged() -> None:
    sid, items = _ready()
    item = items[0]
    state = client.post(
        f"/api/sessions/{sid}/sources/review",
        json={"kind": item["kind"], "key": item["key"], "action": "confirm"},
    ).json()
    assert any(entry["stage"] == "sources" for entry in state["events"])


def test_bad_requests() -> None:
    sid, _ = _ready()
    url = f"/api/sessions/{sid}/sources/review"
    assert client.post(url, json={"kind": "source", "key": "нет-такого", "action": "confirm"}).status_code == 404
    assert client.post(url, json={"kind": "book", "key": "S-1", "action": "confirm"}).status_code == 400
    assert client.post(url, json={"kind": "source", "key": "S-1", "action": "delete"}).status_code == 400
    assert client.get("/api/sessions/no-such/sources/pending").status_code == 404
