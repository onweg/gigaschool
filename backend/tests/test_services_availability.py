"""Инструменты в программе должны работать у слушателей.

Пункт эталонного чек-листа (запрос 2 из data/acr/case-open-requests.docx):
«Все упомянутые ИИ-сервисы доступны в России, а сервисы, которыми в России
нельзя пользоваться напрямую, в программу не вошли».

Правило записано в навыках, но модель про него забывает, поэтому результат
проверяется кодом — и методист видит замечание в отчёте и в .docx.
"""
from __future__ import annotations

import pytest

from app.core.validate import UNAVAILABLE_SERVICES, ValidationReport, check_services
from app.schemas.ir import (
    Hours,
    Module,
    Program,
    ProgramMeta,
    ProgramType,
    SeminarTask,
    Topic,
)


def _program() -> Program:
    return Program(
        meta=ProgramMeta(title="Тест", program_type=ProgramType.DPP_PK, total_hours=72)
    )


@pytest.mark.parametrize("service", sorted(UNAVAILABLE_SERVICES))
def test_each_unavailable_service_is_caught(service: str) -> None:
    program = _program()
    program.goal = f"Научить применять {service} в работе"

    report = ValidationReport()
    check_services(program, report)

    assert [f.code for f in report.findings] == ["SERVICE_UNAVAILABLE_RU"]
    assert report.findings[0].hint, "нужно сказать, чем заменить"


def test_service_inside_a_topic_is_caught() -> None:
    program = _program()
    program.modules = [
        Module(
            id="M-1",
            index=1,
            title="Инструменты",
            hours=Hours(lectures=2),
            topics=[
                Topic(
                    id="T-1.1",
                    code="1.1",
                    title="Генерация изображений",
                    content="Разбираем Midjourney и его настройки",
                    hours=Hours(lectures=2),
                )
            ],
        )
    ]

    report = ValidationReport()
    check_services(program, report)

    assert report.findings
    assert "тема 1.1" in report.findings[0].message


def test_seminar_task_is_checked_too() -> None:
    program = _program()
    program.seminar_tasks = [
        SeminarTask(
            id="SEM-1",
            topic_id="T-1.1",
            title="Конспект урока",
            statement="Составьте план урока в Notion",
        )
    ]

    report = ValidationReport()
    check_services(program, report)

    assert report.findings
    assert "задание SEM-1" in report.findings[0].message


def test_russian_tools_pass() -> None:
    """Доступные инструменты замечаний не вызывают."""
    program = _program()
    program.goal = "Научить работать с GigaChat, YandexGPT, Kandinsky и Jupyter"
    program.annotation = "Практика в Yandex DataSphere и Р7-Офис"

    report = ValidationReport()
    check_services(program, report)

    assert report.findings == []


def test_each_service_reported_once() -> None:
    """Один сервис в пяти местах — одно замечание, а не пять."""
    program = _program()
    program.goal = "ChatGPT для планов"
    program.annotation = "ChatGPT для заданий"
    program.seminar_tasks = [
        SeminarTask(id="SEM-1", topic_id="T-1.1", title="ChatGPT", statement="ChatGPT")
    ]

    report = ValidationReport()
    check_services(program, report)

    assert len(report.findings) == 1
