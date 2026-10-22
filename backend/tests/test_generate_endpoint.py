"""Единый вход /api/generate — тот, через который кейс прогоняют автотестами.

Требования кейса конфликтуют: «вызывается через единый API-эндпоинт» и при этом
«агент задаёт уточняющие вопросы, если данных не хватает». Поэтому адрес один,
а поведений два, и оба обязаны держаться — это и фиксируют тесты.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FULL_REQUEST = (
    "Нужна дополнительная общеразвивающая программа «Первые шаги в Python» для "
    "школьников 8 и 9 классов без опыта программирования. Объем 16 часов, занятия "
    "очно по 2 часа два раза в неделю. В конце каждый ученик защищает проект."
)
VAGUE_REQUEST = (
    "Сделайте курс по кибербезопасности для студентов колледжа, примерно на месяц."
)


def test_full_request_returns_kit_in_one_call() -> None:
    """Данных хватает — комплект приходит сразу, без шага утверждения."""
    response = client.post("/api/generate", json={"text": FULL_REQUEST, "test_total": 10})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "ready"
    assert body["skeleton_auto_approved"] is True
    summary = body["summary"]
    assert summary["total_hours"] == 16
    assert summary["test_questions"] == 10
    assert summary["slides"] > 0
    assert not summary["errors"], summary["errors"]
    assert set(body["exports"]) == {"docx", "pptx", "gift", "csv", "json"}


def test_vague_request_asks_instead_of_inventing() -> None:
    """Данных не хватает — агент спрашивает, а не домысливает тип и объём."""
    response = client.post("/api/generate", json={"text": VAGUE_REQUEST})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "need_answers"
    assert body["session_id"]
    assert "total_hours" in body["missing"]
    # У каждого вопроса есть текст — скрипту проверяющих нужно, что спросили.
    assert all(q["question"] for q in body["questions"])


def test_answers_continue_the_same_session() -> None:
    """Второй вызов с ответами доводит ту же сессию до готового комплекта."""
    first = client.post("/api/generate", json={"text": VAGUE_REQUEST}).json()
    session_id = first["session_id"]

    second = client.post(
        "/api/generate",
        json={
            "session_id": session_id,
            "answers": {
                "program_type": "повышение квалификации",
                "total_hours": "72",
                # Форма обучения в запросе не названа — агент о ней спросил,
                # методист отвечает.
                "study_form": "очно-заочная",
                "entry_level": "базовая компьютерная грамотность",
                "attestation_form": "защита проекта",
            },
        },
    )
    assert second.status_code == 200, second.text
    body = second.json()

    assert body["status"] == "ready"
    assert body["session_id"] == session_id, "сессия должна продолжиться, а не создаться заново"
    assert body["summary"]["total_hours"] == 72


def test_unparsed_answer_is_asked_again() -> None:
    """Непонятый ответ не обрывает прогон: вопрос возвращается с примером."""
    first = client.post("/api/generate", json={"text": VAGUE_REQUEST}).json()

    second = client.post(
        "/api/generate",
        json={
            "session_id": first["session_id"],
            "answers": {"program_type": "ну такое, не знаю", "total_hours": "штук сорок"},
        },
    ).json()

    assert second["status"] == "need_answers"
    assert "program_type" in second["unparsed"]
    assert all(q["question"] for q in second["questions"])


def test_empty_payload_is_rejected() -> None:
    response = client.post("/api/generate", json={})
    assert response.status_code == 400


def test_exported_docx_is_downloadable() -> None:
    body = client.post("/api/generate", json={"text": FULL_REQUEST}).json()
    docx = client.get(body["exports"]["docx"])

    assert docx.status_code == 200
    # PK-заголовок zip: docx собрался, а не пришёл пустой ответ.
    assert docx.content[:2] == b"PK"
