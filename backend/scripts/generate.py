"""Сборка комплекта программы одной командой, без браузера и без сервера.

Нужен для отладки рендеров и для демонстрации: тот же пайплайн, что крутится
за HTTP (intake → skeleton → content → автопроверка → рендеры), но результат
сразу падает файлами в папку.

    python3 scripts/generate.py --type pk --hours 72 \
        --title "Анализ данных на Python" --audience "сотрудники банков" \
        --out ../out

Провайдер выбирается окружением, как и у сервера:
LLM_PROVIDER=mock — офлайн-заглушки; openai_compat + LLM_API_KEY/LLM_MODEL — живая модель.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import build_provider
from app.core.validate import validate_program
from app.pipeline.content import build_content
from app.pipeline.skeleton import build_skeleton
from app.render.docx_acr import render_docx
from app.render.lms import render_gift, render_tests_csv
from app.render.pptx_slides import render_pptx
from app.schemas.ir import ProgramType, StudyForm
from app.schemas.request import ProgramRequest

TYPES = {"pk": ProgramType.DPP_PK, "obsh": ProgramType.DOP_OBSH}
FORMS = {
    "ochnaya": StudyForm.FULL_TIME,
    "ochno_zaochnaya": StudyForm.PART_TIME,
    "zaochnaya": StudyForm.DISTANT,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Собрать комплект программы в файлы")
    p.add_argument("--type", choices=TYPES, default="pk", help="pk — повышение квалификации, obsh — общеразвивающая")
    p.add_argument("--hours", type=float, default=72, help="объём в академических часах")
    p.add_argument("--title", default="Анализ данных на Python")
    p.add_argument("--audience", default="специалисты организации")
    p.add_argument("--level", default="базовый", help="входной уровень слушателей")
    p.add_argument("--form", choices=FORMS, default="ochno_zaochnaya")
    p.add_argument("--attestation", default="защита итогового проекта")
    p.add_argument("--duration", default="", help="срок реализации, например «8 недель по 2 занятия»")
    p.add_argument("--tests", type=int, default=None, help="сколько тестовых вопросов (по умолчанию — от объёма)")
    p.add_argument("--out", default="out", help="папка для файлов")
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    provider = build_provider()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    request = ProgramRequest(
        title=args.title,
        program_type=TYPES[args.type],
        program_type_confidence=1.0,
        total_hours=args.hours,
        audience=args.audience,
        entry_level=args.level,
        study_form=FORMS[args.form],
        attestation_form=args.attestation,
        duration_note=args.duration,
    )

    print(f"провайдер: {provider.name}")
    print("каркас…", flush=True)
    program, usage = await build_skeleton(provider, request)
    print(f"  модулей: {len(program.modules)}, тем: {len(program.all_topics())}, часов: {program.grand_total:g}")

    print("содержание…", flush=True)
    usage += await build_content(
        provider,
        program,
        test_total=args.tests,
        progress=lambda stage, msg: print(f"  {stage}: {msg}", flush=True),
    )

    report = validate_program(program)
    print(f"автопроверка: ошибок {len(report.errors)}, предупреждений {len(report.warnings)}")
    for f in (*report.errors, *report.warnings):
        print(f"  [{f.code}] {f.message}")

    files: dict[str, bytes] = {
        "program.docx": render_docx(program, report),
        "program.json": json.dumps(program.model_dump(mode="json"), ensure_ascii=False, indent=2).encode(),
        "tests.gift": render_gift(program).encode("utf-8"),
        "tests.csv": render_tests_csv(program).encode("utf-8-sig"),
    }
    if program.lecture_plans:
        files["slides.pptx"] = render_pptx(program, program.lecture_plans)

    for name, data in files.items():
        (out / name).write_bytes(data)
        print(f"{out / name}  {len(data) / 1024:.0f} КБ")

    print(f"токенов: {usage.total} (запрос {usage.prompt_tokens}, ответ {usage.completion_tokens})")
    # Ненулевой код — чтобы скрипт было видно в CI, когда комплект не прошёл проверку.
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
