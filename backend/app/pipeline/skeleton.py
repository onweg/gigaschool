"""Шаг 2: каркас программы.

Разделение ответственности:
  модель  → смысл (цель, РО, названия модулей и тем, пропорции);
  код     → числа (часы по модулям, темам и видам работы) и реквизиты
            (нормативная база, профстандарты) из проверенного реестра.
"""
from __future__ import annotations

import json
import re
from math import ceil

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


#: Обломки JSON, которые модель иногда присылает вместо названия: обрезанный
#: ответ, кусок разметки, имя поля с двоеточием. В документ такое попадать не
#: должно — в готовой программе был модуль с названием «final_attestation\': {».
_GARBAGE = re.compile(
    r"""
      ^[\s{}\[\],:"']*$          # одни скобки, кавычки и запятые
    | ^[a-z_]{3,}['\"]?\s*:        # имя поля: final_attestation': {
    | ['\"]\s*:\s*[{\[]           # обрывок пары ключ-значение
    | [{\[]\s*$                    # обрывается на открывающей скобке
    """,
    re.VERBOSE,
)


def looks_like_json_fragment(text: str) -> bool:
    """Строка — обломок разметки, а не осмысленное название."""
    return bool(_GARBAGE.search(text.strip()))


def as_dict(value: object, *, key: str = "title") -> dict:
    """Привести значение к словарю.

    Модели регулярно упрощают структуру: вместо {"title": "..."} возвращают
    просто строку. Падать на этом нельзя — агент обязан доработать то, что
    пришло, поэтому строка разворачивается в объект с одним полем.

    А вот обломки разметки отбрасываем: лучше потерять один элемент, чем
    напечатать в учебном плане строку «final_attestation\': {».
    """
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text:
        return {}
    # Модель могла вернуть объект строкой — разворачиваем, раз он целый.
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = json.loads(text)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if looks_like_json_fragment(text):
        return {}
    return {key: text}


def as_dicts(value: object, *, key: str = "title") -> list[dict]:
    """То же для списка: ["тема А", "тема Б"] → [{"title": "тема А"}, ...].

    Одиночные dict и строка тоже принимаются: модель иногда отдаёт один
    элемент без списка, и терять его нельзя.
    """
    if isinstance(value, (dict, str)):
        value = [value]
    if not isinstance(value, list):
        return []
    out = [as_dict(item, key=key) for item in value]
    return [item for item in out if item]


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
    # В образце общеразвивающей программы АЦР (data/acr/example-dop-obsh)
    # колонки «лабораторные» нет: только лекции, практика и самостоятельная.
    # Лабораторная — тоже занятие в классе, поэтому её доля уходит в практику,
    # а не в самостоятельную работу: иначе расписание «8 занятий по 2 часа»
    # перестанет сходиться с аудиторными часами.
    if request.program_type is ProgramType.DOP_OBSH:
        weights["practice"] += weights["labs"]
        weights["labs"] = 0.0
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
    # Темы, названные методистом прямо, — обязательная часть программы.
    # Без этого модель пропускает их: в живых прогонах SQL и тема об этике
    # исчезали, хотя оба были написаны в запросе буквально.
    if request.must_cover:
        constraints.append(
            'обязательно раскрыть темы, названные методистом: '
            + '; '.join(request.must_cover)
        )
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
    attestation_raw = as_dict(data.get("final_attestation"), key="form")
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
        for i, o in enumerate(as_dicts(data.get("outcomes"), key="text"))
    ]
    known_outcomes = {o.id for o in outcomes}

    competencies: list[Competency] = []
    if program_type is ProgramType.DPP_PK:
        standards = match_prof_standards(
            " ".join([request.direction, request.raw_text, meta.title])
        )
        for i, c in enumerate(as_dicts(data.get("competencies"))):
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
    raw_modules = as_dicts(data.get("modules"))
    module_weights = [float(m.get("weight", 1) or 1) for m in raw_modules]
    module_totals = distribute_modules(total, attestation_hours, module_weights, grain)

    # «Практики не меньше половины всех часов» методист считает от полного
    # объёма программы, а раскладываем мы только часы модулей: аттестация
    # практикой не считается. Поэтому долю пересчитываем на часы модулей,
    # иначе на 16 ч выходит 7 ч вместо требуемых 8.
    module_share = request.min_practice_share
    # «Аудиторных занятий нет» и «доля практики не меньше половины» —
    # взаимоисключающие требования: практика это контактная работа. Структура
    # часов важнее пожелания о доле, иначе солвер перегонит всю самостоятельную
    # работу стажировки в практические занятия, которых не существует.
    if request.no_contact_hours:
        module_share = None
    if module_share is not None:
        modules_total = sum(module_totals)
        module_share = min(1.0, total * module_share / modules_total) if modules_total else None

    modules: list[Module] = []
    for idx, (raw, module_total) in enumerate(zip(raw_modules, module_totals), start=1):
        mix = _mix_weights(as_dict(raw.get("mix")), request)
        module_hours = split_by_kind(
            module_total,
            lectures=mix["lectures"],
            practice=mix["practice"],
            labs=mix["labs"],
            self_study=mix["self_study"],
            min_practice_share=module_share,
            grain=grain,
        )
        raw_topics = as_dicts(raw.get("topics")) or [{"title": raw.get("title", ""), "weight": 1}]
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
        schedule=_schedule_for(request, total),
        entry_requirements=str(data.get("entry_requirements", "") or request.entry_level),
        sources=[
            Source(
                id=f"S-{i + 1}",
                kind=(s.get("kind") or "main"),
                citation=str(s.get("citation", "")).strip(),
                url=s.get("url") or None,
            )
            for i, s in enumerate(as_dicts(data.get("sources"), key="citation"))
        ],
    )
    _attach_orphan_outcomes(program)
    _standards_from_generated_text(program)
    link_competencies(program)
    return program


def _standards_from_generated_text(program: Program) -> None:
    """Подобрать профстандарт по тексту каркаса, если запрос его не подсказал.

    Живой случай: «курс по ИТ-направлению на 200 часов» — в запросе нет ни
    одного слова из реестра, а модель потом пишет цель и результаты про
    Python. Код по-прежнему берётся только из реестра, просто ищем его по
    тексту, где направление уже названо явно.
    """
    if program.meta.program_type is not ProgramType.DPP_PK or program.prof_standards:
        return
    text = " ".join(
        [
            program.meta.title,
            program.meta.direction,
            program.goal,
            *program.objectives,
            *(o.text for o in program.outcomes),
            *(c.title for c in program.competencies),
            *(m.title for m in program.modules),
        ]
    )
    standards = match_prof_standards(text)
    if not standards:
        return
    program.prof_standards = standards
    std = standards[0]
    for competency in program.competencies:
        if not competency.prof_standard_code:
            competency.prof_standard_code = std.code
            competency.labor_function = std.labor_functions[0] if std.labor_functions else None


def link_competencies(program: Program) -> int:
    """Свести карту компетенций в согласованное состояние без модели.

    Модель выдаёт связи с одной стороны (`competency.outcome_ids`) и не
    всегда полно: компетенция без РО, РО без компетенции, ссылки на РО,
    которых нет. Каждое такое место — обрыв трассировки «компетенция → РО →
    оценочное средство». Чиним детерминированно:
      * ссылки на несуществующие РО убираем;
      * РО без компетенции отдаём компетенции, у которой РО меньше всех;
      * компетенции без РО даём РО по кругу из общего списка;
      * обратную ссылку `outcome.competency_id` ставим по первой компетенции.
    Возвращает число исправлений — для журнала.
    """
    if not program.competencies or not program.outcomes:
        return 0
    known = {o.id for o in program.outcomes}
    fixed = 0
    for comp in program.competencies:
        clean = [oid for oid in dict.fromkeys(comp.outcome_ids) if oid in known]
        fixed += len(comp.outcome_ids) - len(clean)
        comp.outcome_ids = clean

    owned = {oid for c in program.competencies for oid in c.outcome_ids}
    for outcome in program.outcomes:
        if outcome.id in owned:
            continue
        target = next(
            (c for c in program.competencies if c.id == outcome.competency_id),
            min(program.competencies, key=lambda c: len(c.outcome_ids)),
        )
        target.outcome_ids.append(outcome.id)
        owned.add(outcome.id)
        fixed += 1

    for index, comp in enumerate(program.competencies):
        if not comp.outcome_ids:
            comp.outcome_ids.append(program.outcomes[index % len(program.outcomes)].id)
            fixed += 1

    first_owner: dict[str, str] = {}
    for comp in program.competencies:
        for oid in comp.outcome_ids:
            first_owner.setdefault(oid, comp.id)
    for outcome in program.outcomes:
        owner = first_owner.get(outcome.id)
        if owner and outcome.competency_id != owner:
            outcome.competency_id = owner
            fixed += 1
    return fixed


def _schedule_for(request: ProgramRequest, total: float) -> Schedule:
    """Календарный график с проверкой ёмкости.

    Срок методист называет приблизительно («около двух месяцев»), а ограничение
    по часам в день — точно. Если в названный срок объём не помещается, срок
    растягиваем: это ближе к намерению, чем выдать график, в который программа
    заведомо не влезает. Расхождение отражаем в примечании, чтобы методист
    увидел, что срок пришлось подвинуть.
    """
    days = request.days
    note = request.duration_note
    if request.max_hours_per_day and total:
        need = ceil(total / request.max_hours_per_day)
        if days is None or days < need:
            if days is not None:
                note = (
                    f"{note + '; ' if note else ''}срок расширен до {need} дней: "
                    f"{total:g} ч при {request.max_hours_per_day:g} ч в день "
                    f"в {days} дней не укладываются"
                ).strip()
            days = need
    weeks = request.weeks
    if days and (weeks is None or weeks * 7 < days):
        weeks = ceil(days / 7)
    return Schedule(
        weeks=weeks,
        days=days,
        sessions=request.sessions,
        hours_per_session=request.hours_per_session,
        max_hours_per_day=request.max_hours_per_day,
        note=note,
    )


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

    # Обратная сторона: тема без единого РО. Такую тему нечем проверять,
    # а вопросы по ней остаются без привязки — это ловит чек-лист кейса.
    # Берём результаты её модуля, иначе первый подходящий по порядку.
    known = {o.id for o in program.outcomes}
    for module in program.modules:
        module_outcomes = [oid for oid in module.outcome_ids if oid in known]
        for index, topic in enumerate(module.topics):
            if topic.outcome_ids:
                continue
            if module_outcomes:
                topic.outcome_ids.append(module_outcomes[index % len(module_outcomes)])
            elif program.outcomes:
                topic.outcome_ids.append(program.outcomes[0].id)
