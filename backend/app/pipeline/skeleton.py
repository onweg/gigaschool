"""Шаг 2: каркас программы.

Разделение ответственности:
  модель  → смысл (цель, РО, названия модулей и тем, пропорции);
  код     → числа (часы по модулям, темам и видам работы) и реквизиты
            (нормативная база, профстандарты) из проверенного реестра.
"""
from __future__ import annotations

from app.core.hours import (
    align_to_grain,
    distribute_modules,
    pick_grain,
    split_by_kind,
    split_exact,
)
from app.llm.base import LLMProvider, Usage
from app.registry.legal import acts_for, match_prof_standards
from app.schemas.ir import (
    Competency,
    FinalAttestation,
    Hours,
    Module,
    Outcome,
    OutcomeKind,
    Program,
    ProgramMeta,
    ProgramType,
    Schedule,
    Source,
    StudyForm,
    Topic,
)
from app.schemas.request import ProgramRequest
from app.skills import load_skill

ISSUED_DOCUMENT = {
    ProgramType.DPP_PK: "удостоверение о повышении квалификации",
    ProgramType.DOP_OBSH: "сертификат об обучении",
}


def default_attestation_hours(total: float, program_type: ProgramType) -> float:
    """Часы итоговой аттестации по умолчанию (образцы АЦР: 24 ч → 6 ч, 248 ч → 2 ч)."""
    if total >= 150:
        return 2.0
    if program_type is ProgramType.DOP_OBSH:
        return min(6.0, max(2.0, round(total * 0.08 * 2) / 2))
    return min(6.0, max(2.0, round(total * 0.05 * 2) / 2))


def contact_only(request: ProgramRequest) -> bool:
    """Весь объём набирается аудиторными занятиями.

    Пример из кейса: «16 часов, занятия очно по 2 часа два раза в неделю» —
    8 занятий × 2 ч = 16 ч, значит часов на самостоятельную работу не остаётся.
    """
    if not (request.sessions and request.hours_per_session and request.total_hours):
        return False
    planned = request.sessions * request.hours_per_session
    return abs(planned - request.total_hours) < 0.01


def _mix_weights(mix: dict, request: ProgramRequest) -> dict[str, float]:
    """Пропорция видов работы. Форма обучения и расписание жёстче мнения модели."""
    if request.no_contact_hours:
        return {"lectures": 0.0, "practice": 0.0, "labs": 0.0, "self_study": 1.0}
    weights = {
        "lectures": float(mix.get("lectures", 1) or 0),
        "practice": float(mix.get("practice", 1) or 0),
        "labs": float(mix.get("labs", 0) or 0),
        "self_study": float(mix.get("self_study", 0) or 0),
    }
    if contact_only(request):
        weights["self_study"] = 0.0
        if weights["lectures"] + weights["practice"] + weights["labs"] <= 0:
            weights["practice"] = 1.0
    elif request.study_form is StudyForm.FULL_TIME:
        # Очная форма: самостоятельная работа не доминирует.
        contact = weights["lectures"] + weights["practice"] + weights["labs"]
        weights["self_study"] = min(weights["self_study"], contact / 3)
    if sum(weights.values()) <= 0:
        weights["self_study"] = 1.0
    return weights


async def build_skeleton(
    provider: LLMProvider,
    request: ProgramRequest,
    *,
    references: list[str] | None = None,
) -> tuple[Program, Usage]:
    if not request.program_type or not request.total_hours:
        raise ValueError("каркас требует типа программы и объёма часов")

    skill = load_skill("skeleton")
    params = [
        f"Тип программы: {'повышение квалификации' if request.program_type is ProgramType.DPP_PK else 'общеразвивающая'}",
        f"Название (если задано): {request.title or '—'}",
        f"Направление: {request.direction or '—'}",
        f"Объём: {request.total_hours:g} академических часов",
        f"Аудитория: {request.audience or '—'}",
        f"Входной уровень: {request.entry_level or '—'}",
        f"Форма обучения: {request.study_form.value if request.study_form else '—'}",
        f"Итоговая аттестация: {request.attestation_form or '—'}",
        f"Срок реализации: {request.duration_note or '—'}",
    ]
    constraints = list(request.constraints)
    if request.min_practice_share:
        constraints.append(
            f"доля практики не менее {int(request.min_practice_share * 100)}% часов"
        )
    if request.no_contact_hours:
        constraints.append(
            "аудиторных занятий нет, вся работа самостоятельная под руководством наставника"
        )
    if request.max_hours_per_day:
        constraints.append(f"не более {request.max_hours_per_day:g} часов в день")
    modules_hint = _modules_hint(request.total_hours)
    constraints.append(modules_hint)

    references_block = ""
    if references:
        references_block = (
            "\nРеференсы методиста (используй структуру, текст не копируй):\n"
            + "\n---\n".join(r[:4000] for r in references)
        )

    data, usage = await provider.complete_json(
        system=skill.system,
        user=skill.render_user(
            params_block="\n".join(params),
            constraints_block="\n".join(f"- {c}" for c in constraints) or "- нет",
            references_block=references_block,
        ),
        max_tokens=6000,
    )
    program = assemble_program(request, data)
    return program, usage


def _modules_hint(total: float) -> str:
    if total <= 24:
        return "структура: 3-4 модуля по 2-3 темы"
    if total <= 72:
        return "структура: 4-6 модулей по 2-4 темы"
    if total <= 200:
        return "структура: 6-9 модулей по 3-5 тем"
    return "структура: 8-12 модулей по 3-6 тем, темы не повторяются"


def assemble_program(request: ProgramRequest, data: dict) -> Program:
    """Собрать Program из ответа модели, посчитав все часы кодом."""
    total = float(request.total_hours or 0)
    program_type = request.program_type or ProgramType.DPP_PK
    attestation_raw = data.get("final_attestation") or {}
    grain = 1.0 if float(total).is_integer() and total >= 16 else pick_grain(total)
    attestation_hours = align_to_grain(
        request.attestation_hours or default_attestation_hours(total, program_type),
        grain,
        minimum=grain,
    )
    # Остаток от деления уходит в итоговую аттестацию, иначе часы «потеряются».
    leftover = round((total - attestation_hours) % grain, 1)
    if leftover:
        attestation_hours = round(attestation_hours + leftover, 1)

    meta = ProgramMeta(
        title=(data.get("title") or request.title or request.direction or "Программа"),
        program_type=program_type,
        total_hours=total,
        direction=request.direction,
        audience=request.audience,
        entry_level=request.entry_level,
        study_form=request.study_form or StudyForm.PART_TIME,
        uses_dot=request.uses_dot,
        issued_document=ISSUED_DOCUMENT[program_type],
        level="базовый",
    )

    outcomes = [
        Outcome(
            id=o.get("id") or f"RO-{i + 1}",
            kind=_outcome_kind(o.get("kind")),
            text=str(o.get("text", "")).strip(),
        )
        for i, o in enumerate(data.get("outcomes") or [])
    ]
    known_outcomes = {o.id for o in outcomes}

    competencies: list[Competency] = []
    if program_type is ProgramType.DPP_PK:
        standards = match_prof_standards(
            " ".join([request.direction, request.raw_text, meta.title])
        )
        for i, c in enumerate(data.get("competencies") or []):
            std = standards[0] if standards else None
            competencies.append(
                Competency(
                    id=c.get("id") or f"PK-{i + 1}",
                    title=str(c.get("title", "")).strip(),
                    activity=str(c.get("activity", "")).strip(),
                    prof_standard_code=std.code if std else None,
                    labor_function=(std.labor_functions[0] if std and std.labor_functions else None),
                    outcome_ids=[x for x in (c.get("outcome_ids") or []) if x in known_outcomes],
                )
            )
    else:
        standards = []

    # ----------------------------- часы ---------------------------------- #
    raw_modules = data.get("modules") or []
    module_weights = [float(m.get("weight", 1) or 1) for m in raw_modules]
    module_totals = distribute_modules(total, attestation_hours, module_weights, grain)

    modules: list[Module] = []
    for idx, (raw, module_total) in enumerate(zip(raw_modules, module_totals), start=1):
        mix = _mix_weights(raw.get("mix") or {}, request)
        module_hours = split_by_kind(
            module_total,
            lectures=mix["lectures"],
            practice=mix["practice"],
            labs=mix["labs"],
            self_study=mix["self_study"],
            min_practice_share=request.min_practice_share,
            grain=grain,
        )
        raw_topics = raw.get("topics") or [{"title": raw.get("title", ""), "weight": 1}]
        topic_weights = [float(t.get("weight", 1) or 1) for t in raw_topics]
        # Часы тем раскладываются по каждому виду работы отдельно — иначе
        # сумма тем по виду не совпадёт со строкой модуля.
        per_kind = {
            kind: split_exact(getattr(module_hours, kind), topic_weights, grain)
            for kind in ("lectures", "practice", "labs", "self_study")
        }
        topics = [
            Topic(
                id=f"T-{idx}.{ti + 1}",
                code=f"{idx}.{ti + 1}",
                title=str(t.get("title", "")).strip(),
                hours=Hours(**{k: per_kind[k][ti] for k in per_kind}),
                outcome_ids=[x for x in (t.get("outcome_ids") or []) if x in known_outcomes],
            )
            for ti, t in enumerate(raw_topics)
        ]
        modules.append(
            Module(
                id=f"M-{idx}",
                index=idx,
                title=str(raw.get("title", "")).strip(),
                hours=module_hours,
                assessment_form=str(raw.get("assessment_form", "") or "").strip(),
                topics=topics,
                outcome_ids=[x for x in (raw.get("outcome_ids") or []) if x in known_outcomes],
            )
        )

    program = Program(
        meta=meta,
        annotation=str(data.get("annotation", "") or "").strip(),
        relevance=str(data.get("relevance", "") or "").strip(),
        goal=str(data.get("goal", "") or "").strip(),
        objectives=[str(x) for x in data.get("objectives") or []],
        legal_acts=acts_for(program_type.value, with_dot=request.uses_dot),
        prof_standards=standards,
        competencies=competencies,
        outcomes=outcomes,
        modules=modules,
        final_attestation=FinalAttestation(
            hours=attestation_hours,
            form=str(attestation_raw.get("form") or request.attestation_form or ""),
            description=str(attestation_raw.get("description", "") or ""),
            criteria=[str(x) for x in attestation_raw.get("criteria") or []],
        ),
        schedule=Schedule(
            weeks=request.weeks,
            days=request.days,
            sessions=request.sessions,
            hours_per_session=request.hours_per_session,
            max_hours_per_day=request.max_hours_per_day,
            note=request.duration_note,
        ),
        entry_requirements=str(data.get("entry_requirements", "") or request.entry_level),
        sources=[
            Source(
                id=f"S-{i + 1}",
                kind=(s.get("kind") or "main"),
                citation=str(s.get("citation", "")).strip(),
                url=s.get("url") or None,
            )
            for i, s in enumerate(data.get("sources") or [])
        ],
    )
    _attach_orphan_outcomes(program)
    return program


def _outcome_kind(value: object) -> OutcomeKind:
    text = str(value or "").lower()
    if "знан" in text:
        return OutcomeKind.KNOWLEDGE
    if "навык" in text:
        return OutcomeKind.ABILITY
    return OutcomeKind.SKILL


def _attach_orphan_outcomes(program: Program) -> None:
    """Каждый РО обязан быть раскрыт хотя бы в одной теме.

    Модель иногда забывает привязку — вместо ошибки чиним детерминированно:
    непривязанный РО уходит в тему модуля, который его заявлял, иначе в первую.
    """
    topics = program.all_topics()
    if not topics:
        return
    covered = {oid for t in topics for oid in t.outcome_ids}
    for outcome in program.outcomes:
        if outcome.id in covered:
            continue
        target = next(
            (
                m.topics[0]
                for m in program.modules
                if outcome.id in m.outcome_ids and m.topics
            ),
            topics[0],
        )
        target.outcome_ids.append(outcome.id)
