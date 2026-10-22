"""Актуализация программы: поиск устаревших тем и инструментов.

Требование кейса «сверх плана» (docs/case-task.md:50). Граница возможностей
здесь честная: устаревание модель определяет по памяти, проверить это нечем,
поэтому агент помечает подозрительное и объясняет, а решает методист.
Тесты фиксируют именно это: находки доходят с причиной и заменой, оговорка
о границах не теряется, мусор от модели не попадает в отчёт.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import READY

from app.llm.base import Completion, LLMProvider, Usage
from app.main import app
from app.pipeline.refresh import content_from_text, find_outdated, program_content
from app.pipeline.skeleton import build_skeleton
from app.llm.mock import MockProvider
from app.schemas.ir import ProgramType
from app.schemas.request import ProgramRequest

client = TestClient(app)

REQUEST = (
    "программа повышения квалификации на 72 часа по анализу данных на Python "
    "для сотрудников банков, очно-заочно, защита проекта"
)


class ExpertProvider(LLMProvider):
    """Модель, которая нашла устаревшее — как на живом прогоне."""

    name = "expert"
    model = "expert"

    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def complete(self, **kwargs) -> Completion:  # noqa: ANN003
        return Completion(text=self.payload, usage=Usage(), model=self.name)


FOUND = """{
  "findings": [
    {"topic_code": "2.1", "what": "pandas 1.x", "why": "вышла версия 2.x, синтаксис изменился",
     "replacement": "pandas 2.x", "severity": "outdated"},
    {"topic_code": "3.4", "what": "OpenAI API", "why": "недоступен в России напрямую",
     "replacement": "GigaChat или YandexGPT", "severity": "outdated"},
    {"topic_code": "1.2", "what": "Jupyter Notebook", "why": "классический интерфейс заморожен",
     "replacement": "JupyterLab", "severity": "aging"},
    {"topic_code": "", "what": "нет темы про векторные базы", "why": "стали стандартом в RAG",
     "replacement": "добавить раздел", "severity": "missing"}
  ],
  "verdict": "Программа в целом жива, но инструменты требуют обновления"
}"""


@pytest.mark.anyio
async def test_findings_carry_reason_and_replacement() -> None:
    """«Устарело» без замены методисту бесполезно."""
    report, _ = await find_outdated(ExpertProvider(FOUND), "какое-то содержание")

    assert report.verdict
    assert len(report.findings) == 4
    for finding in report.findings:
        assert finding.what, "нечего сообщать"
        assert finding.why, f"не сказано, почему {finding.what}"
        assert finding.replacement, f"не сказано, чем заменить {finding.what}"


@pytest.mark.anyio
async def test_urgent_findings_come_first() -> None:
    """Сначала то, что сломается на ближайшем потоке."""
    report, _ = await find_outdated(ExpertProvider(FOUND), "содержание")

    severities = [f.severity for f in report.findings]
    assert severities == sorted(severities, key=lambda s: {"outdated": 0, "aging": 1, "missing": 2}[s])
    assert len(report.outdated) == 2
    assert len(report.missing) == 1


@pytest.mark.anyio
async def test_garbage_from_the_model_is_dropped() -> None:
    """Находка без текста — не находка; неизвестная серьёзность не ломает отчёт."""
    payload = """{"findings": [
        {"what": "", "severity": "outdated"},
        {"what": "   ", "why": "пусто"},
        {"what": "Python 3.7", "severity": "катастрофа", "why": "снят с поддержки",
         "replacement": "Python 3.12"}
    ], "verdict": "ок"}"""

    report, _ = await find_outdated(ExpertProvider(payload), "содержание")

    assert len(report.findings) == 1
    assert report.findings[0].severity == "aging", "неизвестную серьёзность приводим к безопасной"


@pytest.mark.anyio
async def test_empty_content_is_refused() -> None:
    with pytest.raises(ValueError, match="нечего проверять"):
        await find_outdated(ExpertProvider(FOUND), "   ")


@pytest.mark.anyio
async def test_program_content_includes_topics_not_hours() -> None:
    """Актуализация смотрит на содержание: версии живут в темах, а не в часах."""
    request = ProgramRequest(
        title="Анализ данных",
        program_type=ProgramType.DPP_PK,
        total_hours=72,
        audience="сотрудники банков",
        entry_level="базовый",
    )
    program, _ = await build_skeleton(MockProvider(), request)

    content = program_content(program)

    for topic in program.all_topics():
        assert topic.title in content
    assert "ЛК" not in content and "СРС" not in content


def test_content_from_text_is_trimmed() -> None:
    """Файл старой программы может быть огромным — в промпт лезет не всё."""
    text = "\n".join(f"строка {i}" for i in range(5000))

    assert len(content_from_text(text, limit=500)) == 500


def test_endpoint_checks_a_built_program() -> None:
    body = client.post("/api/generate", json={"text": REQUEST, "answers": READY}).json()
    assert body["status"] == "ready", body

    response = client.post(f"/api/sessions/{body['session_id']}/refresh")

    assert response.status_code == 200
    data = response.json()
    assert "disclaimer" in data, "граница возможностей не должна теряться"
    assert "требует проверки методистом" in data["disclaimer"]
    assert set(data["summary"]) == {"outdated", "missing", "total"}
    for finding in data["findings"]:
        assert finding["severity_word"], "серьёзность словом, а не кодом"


def test_endpoint_refuses_without_a_program() -> None:
    body = client.post("/api/generate", json={"text": "курс по чему-нибудь"}).json()

    response = client.post(f"/api/sessions/{body['session_id']}/refresh")

    assert response.status_code == 409


def test_upload_checks_a_foreign_program() -> None:
    """Старой программы в системе может не быть — методист приносит файл."""
    content = (
        "Программа повышения квалификации\n"
        "1. Основы Python 3.7\n"
        "2. Работа с pandas 0.25\n"
        "3. Jupyter Notebook\n"
    ).encode("utf-8")

    response = client.post(
        "/api/refresh/upload",
        files={"file": ("old.txt", content, "text/plain")},
    )

    assert response.status_code == 200
    assert "disclaimer" in response.json()


def test_upload_rejects_broken_docx() -> None:
    response = client.post(
        "/api/refresh/upload",
        files={"file": ("broken.docx", b"not a zip at all", "application/octet-stream")},
    )

    assert response.status_code == 400
