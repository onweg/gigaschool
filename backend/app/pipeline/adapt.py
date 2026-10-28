"""Адаптация готовой программы под другую аудиторию или объём часов.

Требование кейса (docs/case-task.md:51, «Сверх плана»): «Адаптация готовой
программы под другую аудиторию или другой объем часов».

Смысл не в том, чтобы собрать новую программу по изменённому запросу, — это
агент и так умеет. Смысл в переработке: взять то, что уже проверено методистом,
и перенести на новые условия, показав, что именно пришлось изменить.

Исходная программа при этом не трогается: адаптация создаёт отдельную сессию,
методисту нужны обе — старую ещё читают действующие слушатели.
"""
from __future__ import annotations

from app.llm.base import LLMProvider, Usage, as_number
from app.pipeline.skeleton import as_dicts, assemble_program
from app.schemas.ir import Program, ProgramType, StudyForm
from app.schemas.request import ProgramRequest
from app.skills import load_skill

#: Человекочитаемые названия того, что методист может поменять.
FIELD_NAMES = {
    "audience": "аудитория",
    "total_hours": "объём",
    "program_type": "тип программы",
    "study_form": "форма обучения",
    "entry_level": "входной уровень",
    "title": "название",
    "attestation_form": "итоговая аттестация",
}

PROGRAM_TYPE_NAMES = {
    ProgramType.DPP_PK: "повышение квалификации",
    ProgramType.DOP_OBSH: "общеразвивающая",
}


def describe_program(program: Program) -> str:
    """Краткое изложение программы для промпта.

    Целиком её отдавать незачем: модели нужны структура и смысл, а не часы —
    часы всё равно пересчитает солвер.
    """
    lines = [
        f"Название: {program.meta.title}",
        f"Тип: {PROGRAM_TYPE_NAMES.get(program.meta.program_type, '—')}",
        f"Объём: {program.meta.total_hours:g} ак. ч",
        f"Аудитория: {program.meta.audience or '—'}",
        f"Входной уровень: {program.meta.entry_level or '—'}",
        f"Цель: {program.goal}",
        program.learning_preferences.instructions(),
    ]
    if program.outcomes:
        lines.append("Результаты обучения:")
        lines += [f"  {o.id} ({o.kind.value}): {o.text}" for o in program.outcomes]
    lines.append("Структура:")
    for module in program.modules:
        lines.append(f"  {module.index}. {module.title} ({module.hours.total:g} ч)")
        for topic in module.topics:
            linked = ", ".join(topic.outcome_ids) or "—"
            lines.append(f"     {topic.code} {topic.title} [{linked}]")
    if program.final_attestation.form:
        lines.append(
            f"Итоговая аттестация: {program.final_attestation.form} "
            f"({program.final_attestation.hours:g} ч)"
        )
    return "\n".join(lines)


def build_target_request(source: Program, changes: dict) -> ProgramRequest:
    """Запрос на адаптированную программу: исходные параметры плюс правки."""
    meta = source.meta
    request = ProgramRequest(
        **source.learning_preferences.model_dump(),
        title=str(changes.get("title") or meta.title),
        program_type=changes.get("program_type") or meta.program_type,
        program_type_confidence=1.0,
        direction=meta.direction,
        total_hours=as_number(changes.get("total_hours")) or meta.total_hours,
        audience=str(changes.get("audience") or meta.audience),
        entry_level=str(changes.get("entry_level") or meta.entry_level),
        study_form=changes.get("study_form") or meta.study_form,
        uses_dot=meta.uses_dot,
        attestation_form=str(
            changes.get("attestation_form") or source.final_attestation.form or ""
        )
        or None,
    )
    return request


def describe_changes(source: Program, request: ProgramRequest) -> list[str]:
    """Что именно меняется — строками, понятными методисту."""
    meta = source.meta
    diff: list[str] = []
    if request.total_hours != meta.total_hours:
        diff.append(
            f"объём: {meta.total_hours:g} ч → {request.total_hours:g} ч"
        )
    if request.audience != meta.audience:
        diff.append(f"аудитория: «{meta.audience}» → «{request.audience}»")
    if request.program_type is not meta.program_type:
        diff.append(
            f"тип программы: {PROGRAM_TYPE_NAMES.get(meta.program_type)} → "
            f"{PROGRAM_TYPE_NAMES.get(request.program_type)}"
        )
    if request.study_form is not meta.study_form:
        diff.append(
            f"форма обучения: {meta.study_form.value} → "
            f"{request.study_form.value if request.study_form else '—'}"
        )
    if request.entry_level != meta.entry_level:
        diff.append(f"входной уровень: «{meta.entry_level}» → «{request.entry_level}»")
    if request.title != meta.title:
        diff.append(f"название: «{meta.title}» → «{request.title}»")
    return diff


async def adapt_program(
    provider: LLMProvider,
    source: Program,
    changes: dict,
) -> tuple[Program, list[str], Usage]:
    """Переработать программу под новые условия.

    Возвращает новую программу, список изменений от модели и расход токенов.
    Часы считает тот же солвер, что и при первичной сборке, поэтому суммы
    сходятся так же конструктивно.
    """
    request = build_target_request(source, changes)
    diff = describe_changes(source, request)
    if not diff:
        raise ValueError("нечего адаптировать: новые условия совпадают с исходными")

    skill = load_skill("adapt")
    target = [
        f"Тип программы: {PROGRAM_TYPE_NAMES.get(request.program_type, '—')}",
        f"Название: {request.title}",
        f"Объём: {request.total_hours:g} академических часов",
        f"Аудитория: {request.audience or '—'}",
        f"Входной уровень: {request.entry_level or '—'}",
        f"Форма обучения: {request.study_form.value if request.study_form else '—'}",
        f"Итоговая аттестация: {request.attestation_form or '—'}",
    ]
    data, usage = await provider.complete_json(
        system=skill.system,
        user=skill.render_user(
            source_block=describe_program(source),
            target_block="\n".join(target),
            diff_block="\n".join(f"— {line}" for line in diff),
        ),
        max_tokens=4000,
    )

    program = assemble_program(request, data)
    # Общеразвивающая программа с профстандартами — ошибка шаблона; при смене
    # типа модель про это забывает, поэтому чистим здесь, а не ждём отчёта.
    if program.meta.program_type is ProgramType.DOP_OBSH:
        program.prof_standards = []
        program.competencies = []

    # Модель объясняет, чем пришлось пожертвовать: методисту важно знать,
    # что из 72-часовой программы в 36 часов не влезло.
    model_changes = [
        str(item["title"]).strip()
        for item in as_dicts(data.get("changes"), key="title")
        if item.get("title")
    ]
    return program, diff + model_changes, usage
