"""Адаптация готовой программы под другую аудиторию или объём.

Требование кейса «сверх плана» (docs/case-task.md:51). Адаптация — это
переработка проверенной программы, а не сборка новой по изменённому запросу:
структура сохраняется, часы пересчитывает солвер, а методист видит список
того, что пришлось изменить.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import READY

from app.core.validate import validate_program
from app.llm.mock import MockProvider
from app.main import app
from app.pipeline.adapt import adapt_program, describe_program
from app.pipeline.skeleton import build_skeleton
from app.schemas.ir import ProgramType, StudyForm
from app.schemas.request import ProgramRequest

client = TestClient(app)

REQUEST = (
    "программа повышения квалификации на 72 часа по анализу данных на Python "
    "для сотрудников банков, очно-заочно, защита проекта"
)


async def _source_program():  # noqa: ANN202
    request = ProgramRequest(
        title="Анализ данных на Python",
        program_type=ProgramType.DPP_PK,
        total_hours=72,
        audience="сотрудники банков",
        entry_level="уверенная работа в Excel",
        study_form=StudyForm.PART_TIME,
        attestation_form="защита проекта",
    )
    program, _ = await build_skeleton(MockProvider(), request)
    return program


@pytest.mark.anyio
async def test_hours_are_recalculated_exactly() -> None:
    """Объём меняется — суммы сходятся так же, как при первичной сборке."""
    source = await _source_program()

    adapted, _, _ = await adapt_program(MockProvider(), source, {"total_hours": 36})

    assert adapted.grand_total == 36
    report = validate_program(adapted, stage="skeleton")
    assert not [f for f in report.errors if f.code.startswith("HOURS_")]


@pytest.mark.anyio
async def test_source_program_is_untouched() -> None:
    """По исходной программе ещё учатся — её трогать нельзя."""
    source = await _source_program()
    before = describe_program(source)

    await adapt_program(
        MockProvider(), source, {"total_hours": 16, "audience": "школьники"}
    )

    assert describe_program(source) == before
    assert source.meta.total_hours == 72
    assert source.meta.audience == "сотрудники банков"


@pytest.mark.anyio
async def test_switching_template_drops_prof_standards() -> None:
    """У общеразвивающей программы профстандартов быть не должно.

    Это пункт эталонного чек-листа и ошибка шаблона TEMPLATE_OBSH_PROFSTANDARD.
    """
    source = await _source_program()
    assert source.prof_standards, "в исходной программе ПК они есть"

    adapted, _, _ = await adapt_program(
        MockProvider(), source, {"program_type": ProgramType.DOP_OBSH}
    )

    assert adapted.meta.program_type is ProgramType.DOP_OBSH
    assert adapted.prof_standards == []
    assert adapted.competencies == []
    codes = [f.code for f in validate_program(adapted, stage="skeleton").findings]
    assert "TEMPLATE_OBSH_PROFSTANDARD" not in codes


@pytest.mark.anyio
async def test_changes_are_explained_to_the_methodist() -> None:
    """Методист должен видеть, что именно изменилось, а не сравнивать вручную."""
    source = await _source_program()

    _, changes, _ = await adapt_program(
        MockProvider(),
        source,
        {"total_hours": 36, "audience": "студенты колледжа"},
    )

    text = " ".join(changes)
    assert "72" in text and "36" in text
    assert "сотрудники банков" in text and "студенты колледжа" in text


@pytest.mark.anyio
async def test_nothing_to_adapt_is_refused() -> None:
    """Те же условия — не адаптация, а лишний вызов модели."""
    source = await _source_program()

    with pytest.raises(ValueError, match="нечего адаптировать"):
        await adapt_program(
            MockProvider(),
            source,
            {"total_hours": 72, "audience": "сотрудники банков"},
        )


def _ready_session() -> str:
    body = client.post("/api/generate", json={"text": REQUEST, "answers": READY}).json()
    assert body["status"] == "ready", body
    return body["session_id"]


def test_endpoint_creates_a_separate_session() -> None:
    source_id = _ready_session()

    body = client.post(
        f"/api/sessions/{source_id}/adapt",
        json={"total_hours": 36, "audience": "студенты колледжа"},
    ).json()

    assert body["id"] != source_id
    assert body["adapted_from"] == source_id
    assert body["stage"] == "skeleton", "адаптация даёт каркас, его ещё утверждать"
    # Исходная сессия на месте и с прежним объёмом.
    original = client.get(f"/api/sessions/{source_id}").json()
    assert original["program"]["meta"]["total_hours"] == 72


def test_endpoint_refuses_without_a_program() -> None:
    body = client.post("/api/generate", json={"text": "курс по чему-нибудь"}).json()
    assert body["status"] == "need_answers"

    response = client.post(
        f"/api/sessions/{body['session_id']}/adapt", json={"total_hours": 36}
    )
    assert response.status_code == 409


def test_endpoint_refuses_empty_changes() -> None:
    source_id = _ready_session()

    response = client.post(f"/api/sessions/{source_id}/adapt", json={})

    assert response.status_code == 400
    assert "не указано" in response.text


def test_adapted_program_can_be_exported() -> None:
    """Адаптация — полноценная программа: её выгружают так же."""
    source_id = _ready_session()
    body = client.post(
        f"/api/sessions/{source_id}/adapt", json={"total_hours": 36}
    ).json()

    docx = client.get(f"/api/sessions/{body['id']}/export/docx")
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"
