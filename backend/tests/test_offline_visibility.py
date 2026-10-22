"""Работу заглушки нельзя принять за настоящую.

Разбор прогона 19 октября: «mock-режимы ... могут скрывать проблемы в боевом
запуске». У заглушки правильные структура и часы, поэтому её выгрузка легко
сходит за готовую программу. Признак должен быть в каждом ответе и в файле.
"""
from __future__ import annotations

from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from conftest import READY

from app.main import app

client = TestClient(app)

REQUEST = (
    "программа повышения квалификации на 72 часа по анализу данных для банков, "
    "очно-заочно, защита проекта"
)


def _text(data: bytes) -> str:
    return "\n".join(p.text for p in Document(BytesIO(data)).paragraphs)


def test_health_says_it_is_a_stub() -> None:
    body = client.get("/api/health").json()
    assert body["offline_stub"] is True
    assert "заглушка" in body["note"]


def test_every_session_state_carries_the_flag() -> None:
    body = client.post("/api/sessions", json={"text": REQUEST, "answers": READY}).json()
    assert body["offline_stub"] is True


def test_generate_carries_the_flag() -> None:
    body = client.post("/api/generate", json={"text": REQUEST, "answers": READY}).json()
    assert body["status"] == "ready", body
    assert body["offline_stub"] is True


def test_docx_from_stub_is_marked_as_draft() -> None:
    """Файл уходит из интерфейса и живёт отдельно — пометка должна быть в нём."""
    body = client.post("/api/generate", json={"text": REQUEST, "answers": READY}).json()
    docx = client.get(body["exports"]["docx"]).content

    first_page = _text(docx)[:600]
    assert "ЧЕРНОВИК ОФЛАЙН-РЕЖИМА" in first_page


def test_json_export_carries_the_flag() -> None:
    body = client.post("/api/generate", json={"text": REQUEST, "answers": READY}).json()
    bundle = client.get(body["exports"]["json"]).json()
    assert bundle["offline_stub"] is True


def test_live_docx_has_no_stub_notice() -> None:
    """Пометка только для заглушки — настоящий документ ей не засоряем."""
    from app.render.docx_acr import render_docx

    body = client.post("/api/generate", json={"text": REQUEST, "answers": READY}).json()
    session = client.get(f"/api/sessions/{body['session_id']}").json()

    from app.schemas.ir import Program

    program = Program.model_validate(session["program"])
    assert "ЧЕРНОВИК ОФЛАЙН-РЕЖИМА" not in _text(render_docx(program, offline=False))
