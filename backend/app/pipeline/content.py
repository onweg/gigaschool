"""Шаг 3: наполнение каркаса содержанием.

Генерация идёт параллельно по темам (ограничение — семафор), иначе программа
на 500 часов не уложится в тайминг кейса. Каждая тема генерируется независимо,
поэтому точечная доработка одной темы не ломает остальные.
"""
from __future__ import annotations

import asyncio
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
from app.skills import load_skill

MAX_PARALLEL = 4
QUESTIONS_PER_TOPIC = 2


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
    provider: LLMProvider, program: Program, module: Module, topic: Topic
) -> Usage:
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
        ),
        max_tokens=2000,
    )
    topic.content = str(data.get("content", "") or "").strip()
    topic.activities = str(data.get("activities", "") or "").strip()
    return usage


# --------------------------------------------------------------------------- #
# Тесты
# --------------------------------------------------------------------------- #
async def generate_tests(
    provider: LLMProvider,
    program: Program,
    topics: list[Topic],
    *,
    per_topic: int = QUESTIONS_PER_TOPIC,
    total: int | None = None,
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
                    options=[str(o) for o in raw.get("options") or []],
                    correct=[int(i) for i in raw.get("correct") or [0]],
                    explanation=str(raw.get("explanation", "") or ""),
                    difficulty=raw.get("difficulty") or "medium",
                )
            )
    if total and len(program.test_questions) > total:
        del program.test_questions[total:]
    return usage_total


# --------------------------------------------------------------------------- #
# Семинары
# --------------------------------------------------------------------------- #
async def generate_seminars(
    provider: LLMProvider, program: Program, topics: list[Topic], *, per_topic: int = 1
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


# --------------------------------------------------------------------------- #
# Лекции и слайды
# --------------------------------------------------------------------------- #
async def generate_lectures(
    provider: LLMProvider, program: Program, topics: list[Topic]
) -> Usage:
    skill = load_skill("lecture")
    lecture_topics = [t for t in topics if t.hours.lectures > 0] or topics[:1]

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

    target_modules = slides_modules or [program.modules[0].index] if program.modules else []
    slide_topics = [
        t
        for m in program.modules
        if m.index in target_modules
        for t in m.topics
    ]
    say("lectures", f"Планы лекций и слайды ({len(slide_topics)} тем)")
    usage += await generate_lectures(provider, program, slide_topics)
    return usage
