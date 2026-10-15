"""Устойчивость к вольностям модели в структуре ответа.

Живая модель регулярно упрощает JSON: вместо списка объектов отдаёт список
строк, вместо объекта — строку. Это не повод падать с 502 — агент обязан
доработать то, что пришло. Тесты фиксируют поведение, которое уже один раз
сломалось на проде: `'str' object has no attribute 'get'`.
"""
from __future__ import annotations

from app.core.validate import validate_program
from app.pipeline.skeleton import as_dict, as_dicts, assemble_program
from app.schemas.ir import ProgramType
from app.schemas.request import ProgramRequest

#: Ответ, где модель всюду вернула строки вместо объектов.
FLAT_ANSWER = {
    "title": "Анализ данных на Python",
    "goal": "Научить сотрудников банка анализировать данные",
    "annotation": "Программа для аналитиков",
    "entry_requirements": "Базовые навыки работы с таблицами",
    # списки строк вместо списков объектов
    "outcomes": ["знать основы pandas", "уметь строить сводные таблицы"],
    "competencies": ["Анализ банковских данных"],
    "sources": ["Приказ Минобрнауки № 499"],
    # модуль строкой, темы строками, mix строкой
    "modules": [
        "Основы Python",
        {"title": "Pandas", "topics": ["Серии", "Датафреймы"], "mix": "практика"},
    ],
    # объект, свёрнутый в строку
    "final_attestation": "защита проекта",
}


def _request() -> ProgramRequest:
    return ProgramRequest(
        title="Анализ данных",
        program_type=ProgramType.DPP_PK,
        total_hours=72,
        audience="сотрудники банков",
        entry_level="базовый",
    )


def test_strings_instead_of_objects_do_not_crash() -> None:
    """Главный регресс: сборка каркаса не падает на «плоском» ответе."""
    program = assemble_program(_request(), FLAT_ANSWER)

    assert program.modules, "модули должны собраться даже из строк"
    assert program.outcomes, "результаты обучения должны собраться из строк"
    # Строка развернулась в объект с текстом, а не потерялась
    assert any("pandas" in o.text for o in program.outcomes)
    assert program.final_attestation.form == "защита проекта"


def test_hours_still_add_up_on_flat_answer() -> None:
    """Часы считает код, поэтому кривой ответ модели не ломает арифметику."""
    program = assemble_program(_request(), FLAT_ANSWER)
    assert abs(program.grand_total - 72) < 0.01

    report = validate_program(program, stage="skeleton")
    hours_errors = [f for f in report.errors if f.code.startswith("HOURS_")]
    assert not hours_errors, [f.message for f in hours_errors]


def test_normalisers_cover_the_shapes_models_send() -> None:
    assert as_dict("защита проекта", key="form") == {"form": "защита проекта"}
    assert as_dict({"form": "экзамен"}) == {"form": "экзамен"}
    assert as_dict(None) == {}
    assert as_dict(42) == {}

    assert as_dicts(["А", "Б"]) == [{"title": "А"}, {"title": "Б"}]
    assert as_dicts({"title": "один"}) == [{"title": "один"}]
    assert as_dicts("строка") == [{"title": "строка"}]
    assert as_dicts(None) == []
    # Пустые строки не превращаются в пустые объекты
    assert as_dicts(["", "  ", "Б"]) == [{"title": "Б"}]
