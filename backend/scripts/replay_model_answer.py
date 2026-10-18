"""Прогон запроса на заранее заданном ответе модели — без обращения к сети.

Нужен, чтобы разбирать живые сбои даром. Берём ответ, который модель реально
вернула (например GigaChat без `study_form`), подсовываем его агенту и смотрим,
что получилось: какие поля извлеклись, о чём агент спросит, соберётся ли
каркас и сойдутся ли часы.

    # как ответил GigaChat на запросе 2 — без формы обучения
    python3 scripts/replay_model_answer.py --case 2 --drop study_form

    # модель не дала ничего
    python3 scripts/replay_model_answer.py --case 3 --empty

    # свой ответ модели из файла
    python3 scripts/replay_model_answer.py --case 1 --answer answer.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.validate import validate_program
from app.llm.base import Completion, LLMProvider, Usage
from app.llm.mock import MockProvider
from app.pipeline.intake import run_intake
from app.pipeline.skeleton import build_skeleton

#: Тексты запросов кейса — те же, что в scripts/run_open_requests.py.
from scripts.run_open_requests import CASES  # noqa: E402

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
OFF = "\033[0m"

#: Типичный полный ответ модели на intake — основа, из которой выкидываем поля.
FULL_ANSWER = {
    "title": "Программа по запросу методиста",
    "program_type": "dpp_pk",
    "program_type_confidence": 0.9,
    "direction": "ИТ",
    "total_hours": 36,
    "audience": "слушатели по описанию запроса",
    "entry_level": "базовый",
    "study_form": "ochno_zaochnaya",
    "attestation_form": "защита проекта",
    "constraints": [],
}


class ScriptedProvider(LLMProvider):
    """Отдаёт заданный ответ на intake, остальное берёт у заглушки."""

    name = "scripted"
    model = "scripted"

    def __init__(self, answer: dict) -> None:
        self.answer = answer
        self.fallback = MockProvider()

    async def complete(self, *, system: str, user: str, **kwargs) -> Completion:  # noqa: ANN003
        if "SKILL:intake" in system:
            return Completion(
                text=json.dumps(self.answer, ensure_ascii=False), usage=Usage(), model=self.name
            )
        return await self.fallback.complete(system=system, user=user, **kwargs)


def show(request) -> None:  # noqa: ANN001
    fields = (
        ("тип программы", request.program_type.value if request.program_type else None),
        ("объём, ч", request.total_hours),
        ("форма обучения", request.study_form.value if request.study_form else None),
        ("аудитория", request.audience or None),
        ("входной уровень", request.entry_level or None),
        ("аттестация", request.attestation_form),
        ("недель", request.weeks),
        ("без аудиторных", request.no_contact_hours or None),
        ("доля практики", request.min_practice_share),
    )
    for label, value in fields:
        mark = f"{GREEN}+{OFF}" if value not in (None, "") else f"{DIM}—{OFF}"
        print(f"  {mark} {label:<18} {value if value not in (None, '') else ''}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Прогон на заданном ответе модели")
    parser.add_argument("--case", type=int, default=2, choices=[c.num for c in CASES])
    parser.add_argument("--drop", action="append", default=[],
                        help="поле, которое модель «забыла» вернуть")
    parser.add_argument("--empty", action="store_true", help="модель вернула {}")
    parser.add_argument("--answer", help="файл с ответом модели в JSON")
    parser.add_argument("--skeleton", action="store_true",
                        help="собрать каркас и показать часы")
    args = parser.parse_args()

    case = next(c for c in CASES if c.num == args.case)

    if args.empty:
        answer: dict = {}
    elif args.answer:
        answer = json.loads(Path(args.answer).read_text(encoding="utf-8"))
    else:
        answer = dict(FULL_ANSWER)
        for field in args.drop:
            answer.pop(field, None)

    print(f"Запрос {case.num}. {case.name}")
    print(f"{DIM}{case.text[:150]}…{OFF}\n")
    print(f"Ответ модели: {json.dumps(answer, ensure_ascii=False)[:200]}")
    if args.drop:
        print(f"{DIM}(модель «забыла»: {', '.join(args.drop)}){OFF}")

    intake, _ = await run_intake(ScriptedProvider(answer), case.text)
    request = intake.request

    print("\nЧто получилось после разбора:")
    show(request)

    asked = [q.field for q in intake.questions]
    if asked:
        print(f"\nСпросит у методиста ({len(asked)}):")
        for question in intake.questions:
            print(f"  ? {question.field:<18} {question.question}")
    else:
        print(f"\n{GREEN}Вопросов нет — данных хватает{OFF}")

    ready = request.ready()
    verdict = f"{GREEN}каркас соберётся{OFF}" if ready else f"{RED}каркаса не будет{OFF}"
    print(f"\nИтог: {verdict}")

    if ready and args.skeleton:
        program, _ = await build_skeleton(ScriptedProvider(answer), request)
        report = validate_program(program, stage="skeleton")
        print(f"\nКаркас: {len(program.modules)} модулей, "
              f"{len(program.all_topics())} тем, {program.grand_total:g} ч")
        hours = program.modules_hours
        print(f"  ЛК {hours.lectures:g} / ПР {hours.practice:g} / "
              f"ЛБ {hours.labs:g} / СРС {hours.self_study:g} "
              f"+ аттестация {program.final_attestation.hours:g}")
        print(f"  автопроверка: ошибок {len(report.errors)}, "
              f"предупреждений {len(report.warnings)}")
        for finding in report.errors:
            print(f"  {RED}[{finding.code}]{OFF} {finding.message}")

    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
