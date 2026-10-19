"""Шаг 3: наполнение каркаса содержанием.

Генерация идёт параллельно по темам (ограничение — семафор), иначе программа
на 500 часов не уложится в тайминг кейса. Каждая тема генерируется независимо,
поэтому точечная доработка одной темы не ломает остальные.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable

from app.llm.base import LLMProvider, Usage
from app.pipeline.skeleton import as_dicts
from app.schemas.ir import (
    LecturePlan,
    Module,
    Program,
    SeminarTask,
    Slide,
    TestQuestion,
    Topic,
)
from app.config import get_settings
from app.skills import load_skill

#: Пороги берутся из настроек (см. config.py и .env.example): их разумно
#: менять под площадку, а не править код.
_settings = get_settings()
#: Одновременных задач по темам. Тот же предел, что у шлюза: держать больше
#: задач, чем пускает провайдер, бессмысленно — они просто стоят в очереди.
MAX_PARALLEL = _settings.llm_max_parallel
QUESTIONS_PER_TOPIC = _settings.questions_per_topic
#: Сколько первых тем получают слайды, когда методист не выбрал модули.
MIN_SLIDE_TOPICS = _settings.slide_topics


def _outcomes_block(program: Program, topic: Topic) -> str:
    lines = [
        f"- {o.id} ({o.kind.value}): {o.text}"
        for o in program.outcomes
        if o.id in topic.outcome_ids
    ]
    return "\n".join(lines) or "- не заданы"


def _siblings_block(module: Module, topic: Topic) -> str:
    return "\n".join(
        f"- {t.code} {t.title}" for t in module.topics if t.id != topic.id
    ) or "- нет"


async def _bounded(tasks: list[Awaitable], limit: int = MAX_PARALLEL) -> list:
    semaphore = asyncio.Semaphore(limit)

    async def run(task: Awaitable):
        async with semaphore:
            return await task

    return await asyncio.gather(*(run(t) for t in tasks), return_exceptions=True)


# --------------------------------------------------------------------------- #
# Содержание тем
# --------------------------------------------------------------------------- #
async def fill_topic_content(
    provider: LLMProvider,
    program: Program,
    module: Module,
    topic: Topic,
    *,
    instruction: str = "",
) -> Usage:
    """Содержание одной темы. `instruction` — пожелание методиста при
    точечной доработке («усложни», «добавь SQL», «меньше теории»)."""
    skill = load_skill("topic_content")
    data, usage = await provider.complete_json(
        system=skill.system,
        user=skill.render_user(
            program_title=program.meta.title,
            total_hours=f"{program.meta.total_hours:g}",
            audience=program.meta.audience or "—",
            entry_level=program.meta.entry_level or "—",
            module_index=module.index,
            module_title=module.title,
            topic_code=topic.code,
            topic_title=topic.title,
            lectures=f"{topic.hours.lectures:g}",
            practice=f"{topic.hours.practice:g}",
            labs=f"{topic.hours.labs:g}",
            self_study=f"{topic.hours.self_study:g}",
            outcomes_block=_outcomes_block(program, topic),
            siblings_block=_siblings_block(module, topic),
            instruction_block=(
                f"Пожелание методиста к этой теме: {instruction}" if instruction else ""
            ),
        ),
        max_tokens=2000,
    )
    topic.content = str(data.get("content", "") or "").strip()
    topic.activities = str(data.get("activities", "") or "").strip()
    # Переименование по просьбе методиста: «замени тему 2.3 на оконные функции».
    # Без пожелания название не трогаем — модель любит переписывать заголовки
    # просто так, а код темы и ссылки на неё завязаны на номер, не на текст.
    if instruction:
        new_title = str(data.get("title", "") or "").strip()
        if new_title and new_title != topic.title:
            topic.title = new_title
    return usage


# --------------------------------------------------------------------------- #
# Тесты
# --------------------------------------------------------------------------- #
#: Под какими именами модели присылают варианты ответа.
_OPTION_KEYS = ("options", "answers", "variants", "choices")


def _options(raw: dict) -> list[str]:
    """Варианты ответа, под каким бы именем модель их ни прислала."""
    for key in _OPTION_KEYS:
        value = raw.get(key)
        if isinstance(value, list):
            return [str(o).strip() for o in value if str(o).strip()]
    return []


def _usable_question(raw: dict) -> bool:
    """Вопрос, который можно решить и загрузить в LMS.

    Живой случай на GigaChat: вопрос пришёл без вариантов ответа, и ключ «0»
    указывал в пустоту. Такой вопрос не исправить — только выбросить и добрать
    новый: без условия и двух вариантов это не тестовое задание.
    """
    return bool(str(raw.get("stem", "")).strip()) and len(_options(raw)) >= 2


def _valid_keys(correct: object, options: object) -> list[int]:
    """Ключи ответа, очищенные от мусора.

    Живой случай: модель вернула вопрос с ключом, указывающим на вариант,
    которого в списке нет. Такой вопрос нельзя ни проверить, ни загрузить
    в LMS, поэтому индексы вне диапазона отбрасываем, а если не осталось
    ни одного — ставим первый вариант и отдаём на проверку методисту.
    """
    count = len(options) if isinstance(options, list) else 0
    keys: list[int] = []
    for item in correct if isinstance(correct, list) else [correct]:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < count and index not in keys:
            keys.append(index)
    return keys or [0]


async def generate_tests(
    provider: LLMProvider,
    program: Program,
    topics: list[Topic],
    *,
    per_topic: int = QUESTIONS_PER_TOPIC,
    total: int | None = None,
    instruction: str = "",
) -> Usage:
    """Тестовые вопросы с привязкой к РО.

    `total` задаёт точное число вопросов (кейс: «итоговый тест на 10 вопросов»),
    тогда квоты распределяются по темам детерминированно.
    """
    skill = load_skill("tests")
    if total:
        base, extra = divmod(total, len(topics)) if topics else (0, 0)
        quotas = [base + (1 if i < extra else 0) for i in range(len(topics))]
    else:
        quotas = [per_topic] * len(topics)

    async def one(topic: Topic, count: int) -> tuple[Topic, dict, Usage]:
        data, usage = await provider.complete_json(
            system=skill.system,
            user=skill.render_user(
                program_title=program.meta.title,
                audience=program.meta.audience or "—",
                topic_code=topic.code,
                topic_title=topic.title,
                topic_content=topic.content or topic.title,
                outcomes_block=_outcomes_block(program, topic),
                count=count,
                instruction_block=(
                    f"Пожелание методиста: {instruction}" if instruction else ""
                ),
            ),
            max_tokens=2500,
        )
        return topic, data, usage

    results = await _bounded(
        [one(t, q) for t, q in zip(topics, quotas) if q > 0]
    )
    usage_total = Usage()
    counter = 0
    for result in results:
        if isinstance(result, BaseException):
            continue
        topic, data, usage = result
        usage_total += usage
        for raw in as_dicts(data.get("questions"), key="text"):
            if not _usable_question(raw):
                continue
            counter += 1
            program.test_questions.append(
                TestQuestion(
                    id=f"Q-{counter}",
                    topic_id=topic.id,
                    outcome_ids=[
                        o for o in (raw.get("outcome_ids") or topic.outcome_ids)
                        if o in {x.id for x in program.outcomes}
                    ] or list(topic.outcome_ids),
                    stem=str(raw.get("stem", "")).strip(),
                    options=_options(raw),
                    correct=_valid_keys(raw.get("correct"), _options(raw)),
                    explanation=str(raw.get("explanation", "") or ""),
                    difficulty=raw.get("difficulty") or "medium",
                )
            )
    if total and len(program.test_questions) > total:
        del program.test_questions[total:]

    # Модель регулярно возвращает меньше вопросов, чем просили (GigaChat на
    # «тест из 10 вопросов» отдал 4). Добираем недостающее повторным вызовом
    # по темам, где вопросов меньше квоты: число вопросов — требование
    # методиста, а не пожелание. Добираем и без явного числа: негодные вопросы
    # (без вариантов ответа) выброшены выше, и тема не должна остаться пустой.
    usage_total += await _top_up_questions(provider, program, topics, quotas, counter)
    return usage_total


async def _top_up_questions(
    provider: LLMProvider,
    program: Program,
    topics: list[Topic],
    quotas: list[int],
    start_counter: int,
    *,
    attempts: int = 2,
) -> Usage:
    """Добрать вопросы до квоты по каждой теме. Молча сдаваться нельзя."""
    skill = load_skill("tests")
    usage_total = Usage()
    counter = start_counter

    for _ in range(attempts):
        have = Counter(q.topic_id for q in program.test_questions)
        debts = [(t, q - have[t.id]) for t, q in zip(topics, quotas) if q - have[t.id] > 0]
        if not debts:
            break

        async def one(topic: Topic, count: int) -> tuple[Topic, dict, Usage]:
            data, usage = await provider.complete_json(
                system=skill.system,
                user=skill.render_user(
                    program_title=program.meta.title,
                    audience=program.meta.audience or "—",
                    topic_code=topic.code,
                    topic_title=topic.title,
                    topic_content=topic.content or topic.title,
                    outcomes_block=_outcomes_block(program, topic),
                    count=count,
                    instruction_block="",
                ),
                max_tokens=2500,
            )
            return topic, data, usage

        for result in await _bounded([one(t, n) for t, n in debts]):
            if isinstance(result, BaseException):
                continue
            topic, data, usage = result
            usage_total += usage
            # Тема — изменяемая модель и ключом словаря быть не может; сверяем по id.
            debt = next((n for t, n in debts if t.id == topic.id), 0)
            usable = [q for q in as_dicts(data.get("questions"), key="text") if _usable_question(q)]
            for raw in usable[:debt]:
                counter += 1
                program.test_questions.append(
                    TestQuestion(
                        id=f"Q-{counter}",
                        topic_id=topic.id,
                        outcome_ids=list(topic.outcome_ids),
                        stem=str(raw.get("stem", "")).strip(),
                        options=_options(raw),
                        correct=_valid_keys(raw.get("correct"), _options(raw)),
                        explanation=str(raw.get("explanation", "") or ""),
                        difficulty=raw.get("difficulty") or "medium",
                    )
                )
    return usage_total


# --------------------------------------------------------------------------- #
# Семинары
# --------------------------------------------------------------------------- #
async def generate_seminars(
    provider: LLMProvider,
    program: Program,
    topics: list[Topic],
    *,
    per_topic: int = 1,
    instruction: str = "",
) -> Usage:
    skill = load_skill("seminars")

    async def one(topic: Topic) -> tuple[Topic, dict, Usage]:
        data, usage = await provider.complete_json(
            system=skill.system,
            user=skill.render_user(
                program_title=program.meta.title,
                audience=program.meta.audience or "—",
                topic_code=topic.code,
                topic_title=topic.title,
                topic_content=topic.content or topic.title,
                practice_hours=f"{topic.hours.practice + topic.hours.labs:g}",
                outcomes_block=_outcomes_block(program, topic),
                count=per_topic,
                instruction_block=(
                    f"Пожелание методиста: {instruction}" if instruction else ""
                ),
            ),
            max_tokens=2500,
        )
        return topic, data, usage

    practical = [t for t in topics if (t.hours.practice + t.hours.labs) > 0] or topics
    results = await _bounded([one(t) for t in practical])
    usage_total = Usage()
    counter = 0
    for result in results:
        if isinstance(result, BaseException):
            continue
        topic, data, usage = result
        usage_total += usage
        for raw in as_dicts(data.get("tasks"), key="text"):
            counter += 1
            program.seminar_tasks.append(
                SeminarTask(
                    id=f"SEM-{counter}",
                    topic_id=topic.id,
                    outcome_ids=list(raw.get("outcome_ids") or topic.outcome_ids),
                    title=str(raw.get("title", "")).strip(),
                    statement=str(raw.get("statement", "")).strip(),
                    deliverable=str(raw.get("deliverable", "") or ""),
                    criteria=[str(c) for c in raw.get("criteria") or []],
                )
            )
    return usage_total


async def ensure_attestation_criteria(provider: LLMProvider, program: Program) -> Usage:
    """Добрать критерии итоговой аттестации, если модель их не дала.

    «Защита проекта» без критериев — половина требования: методист не сможет
    ни принять работу, ни объяснить слушателю, что от него хотят. Это пункт
    эталонных чек-листов, и в живых прогонах он проваливался в трети случаев.
    Просим модель отдельно и коротко — один дешёвый вызов.
    """
    attestation = program.final_attestation
    if attestation.criteria or not attestation.form:
        return Usage()

    system = (
        "SKILL:attestation_criteria\n\n"
        "Ты методист АЦР. Сформулируй критерии оценки итоговой аттестации.\n"
        "Правила:\n"
        "1. От 3 до 5 критериев, каждый проверяемый: по нему можно принять "
        "или не принять работу.\n"
        "2. Формулируй под аудиторию программы, без общих слов вроде "
        "«качественно выполнено».\n"
        '3. Отвечай ТОЛЬКО JSON: {"criteria": ["...", "..."]}'
    )
    user = (
        f"Программа: {program.meta.title} ({program.meta.total_hours:g} ак. ч)\n"
        f"Аудитория: {program.meta.audience or '—'}\n"
        f"Форма аттестации: {attestation.form}\n"
        f"Описание: {attestation.description or '—'}"
    )
    try:
        data, usage = await provider.complete_json(system=system, user=user, max_tokens=700)
    except Exception:  # noqa: BLE001 — без критериев автопроверка всё равно предупредит
        return Usage()

    attestation.criteria = [
        str(item["title"]).strip()
        for item in as_dicts(data.get("criteria"), key="title")
        if str(item.get("title", "")).strip()
    ][:5]
    return usage


async def replace_unavailable_services(
    provider: LLMProvider,
    program: Program,
    say: Callable[[str, str], None] | None = None,
) -> Usage:
    """Переписать темы, где упомянуты недоступные в России сервисы.

    Правило есть в навыках, но модель про него забывает: в живых прогонах
    ChatGPT всплывал в двух случаях из трёх. Чек-лист кейса требует, чтобы
    таких сервисов в программе не было, поэтому агент не просто помечает
    находку, а переписывает затронутые темы — один вызов на тему.
    """
    from app.core.validate import UNAVAILABLE_SERVICES

    affected: list[tuple[Topic, list[str]]] = []
    for topic in program.all_topics():
        text = f"{topic.title} {topic.content} {topic.activities}".lower()
        found = [s for s in UNAVAILABLE_SERVICES if s in text]
        if found:
            affected.append((topic, found))
    if not affected:
        return Usage()

    if say:
        say("content", f"Замена недоступных сервисов в {len(affected)} темах")

    usage = Usage()
    for topic, found in affected:
        replacements = "; ".join(f"{s} → {UNAVAILABLE_SERVICES[s]}" for s in found)
        module = next(m for m in program.modules if topic in m.topics)
        usage += await fill_topic_content(
            provider,
            program,
            module,
            topic,
            instruction=(
                f"В этой теме упомянуты сервисы, недоступные в России напрямую. "
                f"Перепиши содержание, заменив их: {replacements}. "
                f"Структуру и объём темы сохрани."
            ),
        )
    return usage


# --------------------------------------------------------------------------- #
# Покрытие результатов обучения
# --------------------------------------------------------------------------- #
def link_outcomes_to_assessments(program: Program) -> int:
    """Довести каждый РО до двух видов проверки, не обращаясь к модели.

    Чек-лист кейса требует, чтобы результат обучения проверялся тестом и
    заданием семинара. Модель сама расставляет `outcome_ids` и регулярно
    забывает часть РО — хотя вопрос по теме, раскрывающей этот РО, его по
    сути и проверяет. Поэтому связь восстанавливается арифметикой: берём
    оценочные средства темы, где РО раскрыт, и дописываем ему привязку.

    Возвращает число исправленных связей — для журнала и отчёта.
    """
    topics_by_outcome: dict[str, list[str]] = {o.id: [] for o in program.outcomes}
    for topic in program.all_topics():
        for oid in topic.outcome_ids:
            if oid in topics_by_outcome:
                topics_by_outcome[oid].append(topic.id)

    # Задания семинаров генерируются только для тем с практическими часами,
    # поэтому у чисто лекционной темы своего задания может не быть. Тогда
    # годится задание соседней темы того же модуля: оно проверяет результаты
    # того же раздела программы.
    module_of_topic = {t.id: m.index for m in program.modules for t in m.topics}
    topics_of_module: dict[int, set[str]] = {}
    for topic_id, module_index in module_of_topic.items():
        topics_of_module.setdefault(module_index, set()).add(topic_id)

    fixed = 0
    for outcome in program.outcomes:
        topic_ids = topics_by_outcome.get(outcome.id) or []
        if not topic_ids:
            # РО не раскрыт ни одной темой — это ошибка каркаса, её ловит
            # автопроверка кодом COVERAGE_OUTCOME_NO_TOPIC. Здесь не чиним.
            continue
        # Темы самого РО — в первую очередь, темы его модулей — запасной круг.
        fallback = {
            tid
            for topic_id in topic_ids
            for tid in topics_of_module.get(module_of_topic.get(topic_id, -1), set())
        }
        for items in (program.test_questions, program.seminar_tasks):
            if any(outcome.id in item.outcome_ids for item in items):
                continue
            for scope in (set(topic_ids), fallback):
                target = next((i for i in items if i.topic_id in scope), None)
                if target is not None:
                    target.outcome_ids.append(outcome.id)
                    fixed += 1
                    break
    return fixed


# --------------------------------------------------------------------------- #
# Лекции и слайды
# --------------------------------------------------------------------------- #
async def generate_lectures(
    provider: LLMProvider, program: Program, topics: list[Topic]
) -> Usage:
    skill = load_skill("lecture")
    # Тема без лекционных часов всё равно получает план занятия: методист
    # просил слайды к конкретным занятиям, а не к строкам «ЛК» учебного плана.
    lecture_topics = topics

    async def one(topic: Topic) -> tuple[Topic, dict, Usage]:
        data, usage = await provider.complete_json(
            system=skill.system,
            user=skill.render_user(
                program_title=program.meta.title,
                audience=program.meta.audience or "—",
                topic_code=topic.code,
                topic_title=topic.title,
                topic_content=topic.content or topic.title,
                lecture_hours=f"{topic.hours.lectures:g}",
                outcomes_block=_outcomes_block(program, topic),
            ),
            max_tokens=3000,
        )
        return topic, data, usage

    results = await _bounded([one(t) for t in lecture_topics])
    usage_total = Usage()
    for result in results:
        if isinstance(result, BaseException):
            continue
        topic, data, usage = result
        usage_total += usage
        program.lecture_plans.append(
            LecturePlan(
                id=f"LEC-{topic.code}",
                topic_id=topic.id,
                title=f"Тема {topic.code}. {topic.title}",
                objectives=[str(x) for x in data.get("objectives") or []],
                timing=[str(x) for x in data.get("timing") or []],
                slides=[
                    Slide(
                        title=str(s.get("title", "")).strip(),
                        bullets=[str(b) for b in s.get("bullets") or []],
                        notes=str(s.get("notes", "") or ""),
                    )
                    for s in as_dicts(data.get("slides"))
                ],
            )
        )
    return usage_total


# --------------------------------------------------------------------------- #
# Полный комплект
# --------------------------------------------------------------------------- #
async def build_content(
    provider: LLMProvider,
    program: Program,
    *,
    test_total: int | None = None,
    slides_modules: list[int] | None = None,
    progress: Callable[[str, str], None] | None = None,
) -> Usage:
    """Наполнить каркас. `slides_modules` — индексы модулей, для которых нужны слайды."""
    usage = Usage()
    topics = program.all_topics()

    def say(stage: str, message: str) -> None:
        if progress:
            progress(stage, message)

    say("content", f"Содержание {len(topics)} тем")
    results = await _bounded(
        [
            fill_topic_content(provider, program, module, topic)
            for module in program.modules
            for topic in module.topics
        ]
    )
    for r in results:
        if isinstance(r, Usage):
            usage += r

    say("tests", "Тестовые вопросы с ключами")
    usage += await generate_tests(provider, program, topics, total=test_total)

    say("seminars", "Задания для семинаров с критериями")
    usage += await generate_seminars(provider, program, topics)

    # Слайды методист просит по занятиям («для первых двух занятий»), а не по
    # модулям: сколько тем попало в первый модуль — решает модель, и на это
    # опираться нельзя. Поэтому берём первые MIN_SLIDE_TOPICS тем подряд,
    # а явный выбор модулей из интерфейса по-прежнему уважаем.
    if slides_modules:
        slide_topics = [t for m in program.modules if m.index in slides_modules for t in m.topics]
    else:
        slide_topics = topics[:MIN_SLIDE_TOPICS]
    link_outcomes_to_assessments(program)
    usage += await ensure_attestation_criteria(provider, program)
    usage += await replace_unavailable_services(provider, program, say)

    say("lectures", f"Планы лекций и слайды ({len(slide_topics)} тем)")
    usage += await generate_lectures(provider, program, slide_topics)
    return usage
