"""Выгрузка тестов в форматы LMS.

GIFT — текстовый формат импорта вопросов Moodle: одна строка на вопрос,
`=` помечает верный вариант, `~` неверный. Дополнительно CSV для LMS,
которые GIFT не понимают.
"""
from __future__ import annotations

import csv
import io

from app.schemas.ir import Program

_ESCAPE = str.maketrans({c: f"\\{c}" for c in "~=#{}:"})


def _esc(text: str) -> str:
    return text.replace("\n", " ").translate(_ESCAPE).strip()


def render_gift(program: Program) -> str:
    lines: list[str] = [
        f"// Банк тестовых вопросов: {program.meta.title}",
        f"// Объём программы: {program.meta.total_hours:g} ак. ч",
        "",
    ]
    for q in program.test_questions:
        topic = program.topic_by_id(q.topic_id) if q.topic_id else None
        category = f"$CATEGORY: {program.meta.title}/" + (
            f"Тема {topic.code}" if topic else "Общие"
        )
        lines.append(category)
        trace = f" [{', '.join(q.outcome_ids)}]" if q.outcome_ids else ""
        lines.append(f"::{q.id}{trace}::{_esc(q.stem)} {{")
        for i, option in enumerate(q.options):
            mark = "=" if i in q.correct else "~"
            lines.append(f"\t{mark}{_esc(option)}")
        if q.explanation:
            lines.append(f"\t#### {_esc(q.explanation)}")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def render_tests_csv(program: Program) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        ["id", "тема", "вопрос", "вариант 1", "вариант 2", "вариант 3", "вариант 4",
         "верный", "пояснение", "результаты обучения", "сложность"]
    )
    for q in program.test_questions:
        topic = program.topic_by_id(q.topic_id) if q.topic_id else None
        options = (q.options + [""] * 4)[:4]
        keys = ", ".join(str(i + 1) for i in q.correct)
        writer.writerow(
            [q.id, topic.code if topic else "", q.stem, *options, keys,
             q.explanation, ", ".join(q.outcome_ids), q.difficulty]
        )
    return buffer.getvalue()
