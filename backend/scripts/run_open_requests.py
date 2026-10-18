"""Прогон пяти открытых запросов кейса с проверкой эталонных чек-листов.

Запросы и чек-листы взяты из data/acr/case-open-requests.docx дословно.
Организаторы прогонят агента на этом наборе плюс на скрытых запросах, поэтому
провал здесь — это провал на защите, увиденный заранее.

    LLM_PROVIDER=mock python3 scripts/run_open_requests.py
    LLM_PROVIDER=mock python3 scripts/run_open_requests.py --only 4

Что проверяется кодом: выбор шаблона, суммы часов, доля практики, график,
число тестовых вопросов, покрытие результатов обучения оценочными средствами,
пустые реквизиты приказа. Смысл формулировок кодом не проверить — это на глаз.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import build_provider
from app.core.validate import validate_program
from app.pipeline.content import build_content
from app.pipeline.intake import apply_answers, run_intake
from app.pipeline.skeleton import build_skeleton
from app.render.docx_acr import render_docx
from app.render.lms import render_gift
from app.render.pptx_slides import render_pptx
from app.schemas.ir import Program, ProgramType, VerificationStatus

Check = Callable[[Program], tuple[bool, str]]

#: Проверки содержания: заглушка пишет болванки и провалит их по определению,
#: поэтому на mock они пропускаются, а на живой модели — обязательны.
NEEDS_MODEL: set[str] = set()


def content_check(title: str, check: Check) -> tuple[str, Check]:
    """Пометить проверку как требующую живой модели."""
    NEEDS_MODEL.add(title)
    return title, check


def hours_eq(expected: float) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        got = p.grand_total
        return abs(got - expected) < 0.01, f"объём {got:g} ч, ожидалось {expected:g}"

    return check


def template_is(expected: ProgramType) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        got = p.meta.program_type
        return got is expected, f"шаблон {got.value}, ожидался {expected.value}"

    return check


def practice_at_least(hours: float) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        got = p.modules_hours.practice + p.modules_hours.labs
        return got >= hours, f"практики {got:g} ч, нужно не меньше {hours:g}"

    return check


def no_contact_hours() -> Check:
    def check(p: Program) -> tuple[bool, str]:
        h = p.modules_hours
        got = h.lectures + h.practice + h.labs
        return got == 0, f"аудиторных {got:g} ч, в стажировке их быть не должно"

    return check


def questions_count(expected: int) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        got = len(p.test_questions)
        return got == expected, f"вопросов {got}, в запросе просили {expected}"

    return check


def weeks_eq(expected: int) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        got = p.schedule.weeks
        return got == expected, f"график на {got} нед., ожидалось {expected}"

    return check


def attestation_hours_eq(expected: float) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        got = p.final_attestation.hours
        return abs(got - expected) < 0.01, f"аттестация {got:g} ч, ожидалось {expected:g}"

    return check


def prof_standards(present: bool) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        got = len(p.prof_standards)
        ok = bool(got) is present
        want = "должны быть" if present else "не требуются для этого шаблона"
        return ok, f"профстандартов {got}, {want}"

    return check


def slides_for_first_topics(count: int) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        topics = [t.id for t in p.all_topics()[:count]]
        covered = {plan.topic_id for plan in p.lecture_plans}
        missing = [t for t in topics if t not in covered]
        return not missing, f"слайды есть для {len(covered)} тем, не хватает на первые {len(missing)}"

    return check




# ------------------- проверки по эталонным чек-листам кейса ---------------- #
# Каждая ниже соответствует строке из data/acr/case-open-requests.docx.
# Смысл формулировок кодом не проверить — это читают глазами; здесь только
# то, что можно посчитать.


def _all_text(p: Program) -> str:
    """Весь осмысленный текст программы одной строкой — для поиска по темам."""
    parts = [p.goal, p.annotation, p.relevance, p.entry_requirements]
    parts += [o.text for o in p.outcomes]
    for topic in p.all_topics():
        parts += [topic.title, topic.content, topic.activities]
    for module in p.modules:
        parts.append(module.title)
    parts.append(p.final_attestation.form)
    parts.append(p.final_attestation.description)
    parts += p.final_attestation.criteria
    for task in p.seminar_tasks:
        parts += [task.title, task.statement]
    return " ".join(x for x in parts if x).lower()


def covers_topics(*groups: tuple[str, ...]) -> Check:
    """Каждая тема из запроса должна где-то раскрываться."""

    def check(p: Program) -> tuple[bool, str]:
        text = _all_text(p)
        missing = [g[0] for g in groups if not any(word.lower() in text for word in g)]
        return not missing, f"не раскрыты: {', '.join(missing) or 'ничего'}"

    return check


def mentions(*words: str, what: str = "") -> Check:
    """В программе есть упоминание хотя бы одного из слов."""

    def check(p: Program) -> tuple[bool, str]:
        text = _all_text(p)
        hit = next((w for w in words if w.lower() in text), None)
        return bool(hit), f"{what or 'упоминание'}: {hit or 'не найдено'}"

    return check


def issued_document_mentions(word: str) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        got = p.meta.issued_document or "—"
        return word.lower() in got.lower(), f"выдаётся: {got}"

    return check


def attestation_is(*words: str) -> Check:
    """Форма итоговой аттестации из запроса методиста."""

    def check(p: Program) -> tuple[bool, str]:
        form = (p.final_attestation.form or "").lower()
        hit = any(w.lower() in form for w in words)
        return hit, f"аттестация: «{p.final_attestation.form or '—'}»"

    return check


def attestation_has_criteria(least: int = 1) -> Check:
    """«с критериями оценки» — отдельная строка в каждом чек-листе."""

    def check(p: Program) -> tuple[bool, str]:
        got = len(p.final_attestation.criteria)
        return got >= least, f"критериев аттестации {got}, нужно не меньше {least}"

    return check


def questions_have_keys() -> Check:
    """«Тест содержит вопросы с ключами» — без правильных ответов тест бесполезен."""

    def check(p: Program) -> tuple[bool, str]:
        broken = [
            q.id
            for q in p.test_questions
            if not q.correct or max(q.correct, default=-1) >= len(q.options)
        ]
        return not broken, f"без ключа или с неверным ключом: {len(broken)}"

    return check


def questions_linked_to_outcomes() -> Check:
    """«каждый вопрос привязан к результату обучения»."""

    def check(p: Program) -> tuple[bool, str]:
        known = {o.id for o in p.outcomes}
        orphans = [q.id for q in p.test_questions if not (set(q.outcome_ids) & known)]
        return not orphans, f"вопросов без привязки к РО: {len(orphans)}"

    return check


def seminars_have_criteria() -> Check:
    """«Задания для семинаров с критериями оценки» — обязательный пункт ТЗ."""

    def check(p: Program) -> tuple[bool, str]:
        without = [s.id for s in p.seminar_tasks if not s.criteria]
        return not without, f"заданий без критериев: {len(without)} из {len(p.seminar_tasks)}"

    return check


def slides_have_content() -> Check:
    """Слайды должны содержать тезисы, а не пустые заголовки."""

    def check(p: Program) -> tuple[bool, str]:
        slides = [s for plan in p.lecture_plans for s in plan.slides]
        empty = [s.title for s in slides if not s.bullets]
        return bool(slides) and not empty, (
            f"слайдов {len(slides)}, без тезисов {len(empty)}"
        )

    return check


def self_study_eq(expected: float) -> Check:
    def check(p: Program) -> tuple[bool, str]:
        got = p.modules_hours.self_study
        return abs(got - expected) < 0.01, f"СРС {got:g} ч, ожидалось {expected:g}"

    return check


def sessions_schedule(count: int, hours_each: float) -> Check:
    """«Календарный график состоит из 8 занятий по 2 часа»."""

    def check(p: Program) -> tuple[bool, str]:
        s = p.schedule
        ok = s.sessions == count and (s.hours_per_session or 0) == hours_each
        return ok, (
            f"график: {s.sessions or '—'} занятий по "
            f"{s.hours_per_session or '—'} ч, ожидалось {count}×{hours_each:g}"
        )

    return check


def daily_load_at_most(hours: float) -> Check:
    """«нагрузка не превышает 4 часов в день»."""

    def check(p: Program) -> tuple[bool, str]:
        got = p.schedule.max_hours_per_day
        return got is not None and got <= hours, (
            f"нагрузка в день: {got or '—'} ч, допустимо не больше {hours:g}"
        )

    return check


def months_about(expected: int, tolerance: int = 2) -> Check:
    """«срок около двух месяцев» — проверяем недели с допуском."""

    def check(p: Program) -> tuple[bool, str]:
        weeks = p.schedule.weeks
        want = expected * 4
        ok = weeks is not None and abs(weeks - want) <= tolerance
        return ok, f"срок {weeks or '—'} нед., ожидалось около {want}"

    return check


def no_unavailable_services() -> Check:
    """«Все упомянутые ИИ-сервисы доступны в России»."""

    def check(p: Program) -> tuple[bool, str]:
        hits = [
            f.message.split("«")[1].split("»")[0]
            for f in validate_program(p).findings
            if f.code == "SERVICE_UNAVAILABLE_RU"
        ]
        return not hits, f"недоступные сервисы: {', '.join(hits) or 'нет'}"

    return check


def sources_marked() -> Check:
    """«неподтверждённый источник агент должен явно помечать» — требование ТЗ."""

    def check(p: Program) -> tuple[bool, str]:
        unverified = [
            s for s in (*p.prof_standards, *p.legal_acts)
            if s.verification is not VerificationStatus.VERIFIED
        ]
        codes = {f.code for f in validate_program(p).findings}
        ok = not unverified or bool(
            {"SOURCE_UNVERIFIED", "SOURCES_UNVERIFIED_LIST"} & codes
        )
        return ok, (
            f"неподтверждённых источников {len(unverified)}, "
            f"{'помечены' if ok else 'НЕ помечены'}"
        )

    return check


def outcomes_count_between(low: int, high: int) -> Check:
    """Результатов обучения должно быть разумное число для объёма."""

    def check(p: Program) -> tuple[bool, str]:
        got = len(p.outcomes)
        return low <= got <= high, f"результатов обучения {got}, ожидалось {low}–{high}"

    return check


#: Обещания, которых не бывает за 72 часа. Ищем обороты целиком: слово
#: «профессиональный» само по себе нормально — «профессиональная деятельность»
#: встречается в каждой второй программе ПК.
OVERPROMISE = (
    "стать специалистом",
    "стать профессионалом",
    "полноценным специалистом",
    "готовым специалистом",
    "полная подготовка",
    "стать экспертом",
    "уровень эксперта",
    "начать работать по специальности",
)


def no_overpromise() -> Check:
    """«Не обещай подготовку специалиста за 72 часа» — пункт чек-листа запроса 4."""

    def check(p: Program) -> tuple[bool, str]:
        text = " ".join(o.text for o in p.outcomes).lower() + " " + p.goal.lower()
        found = [w for w in OVERPROMISE if w in text]
        return not found, f"завышенные обещания: {', '.join(found) or 'нет'}"

    return check


def exportable(*kinds: str) -> Check:
    """Комплект выгружается в нужных форматах — требование ТЗ."""

    def check(p: Program) -> tuple[bool, str]:
        problems: list[str] = []
        for kind in kinds:
            try:
                if kind == "docx":
                    data = render_docx(p, validate_program(p))
                    if data[:2] != b"PK":
                        problems.append("docx")
                elif kind == "pptx":
                    if not p.lecture_plans:
                        problems.append("pptx: нет планов лекций")
                    elif render_pptx(p, p.lecture_plans)[:2] != b"PK":
                        problems.append("pptx")
                elif kind == "gift":
                    if not render_gift(p).strip():
                        problems.append("gift")
                elif kind == "json":
                    json.dumps(p.model_dump(mode="json"), ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{kind}: {exc}")
        return not problems, f"выгрузки: {', '.join(problems) or 'все собираются'}"

    return check


def russian_language() -> Check:
    """«Материалы готовятся на русском языке» — требование ТЗ."""

    def check(p: Program) -> tuple[bool, str]:
        text = _all_text(p)
        cyrillic = sum(1 for ch in text if "а" <= ch <= "я" or ch == "ё")
        latin = sum(1 for ch in text if "a" <= ch <= "z")
        ok = cyrillic > latin
        return ok, f"кириллицы {cyrillic}, латиницы {latin}"

    return check


# ----------------------- проверки, общие для всех запросов ----------------- #


def hours_consistent() -> Check:
    def check(p: Program) -> tuple[bool, str]:
        bad = validate_program(p).errors
        codes = [f.code for f in bad if f.code.startswith("HOURS_")]
        return not codes, f"расхождения часов: {', '.join(codes) or 'нет'}"

    return check


def outcomes_double_checked() -> Check:
    """Чек-лист кейса: каждый результат обучения проверяется не менее двух раз."""

    def check(p: Program) -> tuple[bool, str]:
        weak: list[str] = []
        for outcome in p.outcomes:
            hits = sum(
                1
                for item in (*p.test_questions, *p.seminar_tasks)
                if outcome.id in item.outcome_ids
            )
            if hits < 2:
                weak.append(f"{outcome.id}({hits})")
        return not weak, f"РО с одним средством или без: {', '.join(weak) or 'нет'}"

    return check


def order_fields_empty() -> Check:
    def check(p: Program) -> tuple[bool, str]:
        filled = [
            name
            for name, value in (("номер приказа", p.meta.order_number), ("дата", p.meta.order_date))
            if value
        ]
        return not filled, f"заполнено агентом: {', '.join(filled) or 'ничего, верно'}"

    return check


COMMON: list[tuple[str, Check]] = [
    ("часы сходятся на всех уровнях", hours_consistent()),
    ("каждый РО проверяется двумя средствами", outcomes_double_checked()),
    ("реквизиты приказа оставлены пустыми", order_fields_empty()),
    # Общие требования из шапки чек-листов и из ТЗ.
    ("тест с ключами", questions_have_keys()),
    ("вопросы привязаны к РО", questions_linked_to_outcomes()),
    ("задания семинаров с критериями", seminars_have_criteria()),
    content_check("слайды с тезисами, а не пустые", slides_have_content()),
    ("неподтверждённые источники помечены", sources_marked()),
    content_check("сервисы доступны в России", no_unavailable_services()),
    ("материалы на русском языке", russian_language()),
    ("комплект выгружается", exportable("docx", "pptx", "gift", "json")),
]


@dataclass
class Case:
    num: int
    name: str
    text: str
    #: Ответы «методиста» на уточняющие вопросы агента.
    answers: dict[str, str] = field(default_factory=dict)
    checks: list[tuple[str, Check]] = field(default_factory=list)
    #: True — агент обязан задать вопросы, а не домыслить параметры.
    expect_questions: bool = False
    tests: int | None = None


CASES = [
    Case(
        num=1,
        name="«Первые шаги в Python» для школьников",
        text=(
            "Нужна дополнительная общеразвивающая программа «Первые шаги в Python» для "
            "школьников 8 и 9 классов без опыта программирования. Объем 16 часов, занятия "
            "очно по 2 часа два раза в неделю. Практики должно быть не меньше половины всех "
            "часов. В конце каждый ученик защищает небольшой проект, например простую игру "
            "или чат-бота. Нужны итоговый тест на 10 вопросов и слайды для первых двух занятий."
        ),
        answers={
            "entry_level": "без опыта программирования",
            "audience": "школьники 8–9 классов",
        },
        tests=10,
        checks=[
            ("выбран шаблон общеразвивающей", template_is(ProgramType.DOP_OBSH)),
            ("объём ровно 16 ч", hours_eq(16)),
            ("практики не меньше 8 ч", practice_at_least(8)),
            ("график укладывается в 4 недели", weeks_eq(4)),
            ("тест на 10 вопросов", questions_count(10)),
            ("слайды для первых двух занятий", slides_for_first_topics(2)),
            ("профстандартов нет", prof_standards(False)),
            ("график 8 занятий по 2 часа", sessions_schedule(8, 2)),
            content_check("итоговая аттестация — защита проекта", attestation_is("защит", "проект")),
            content_check("у аттестации есть критерии", attestation_has_criteria(2)),
            content_check("темы для нулевого уровня раскрыты",
             covers_topics(
                 ("переменн", "тип данных"),
                 ("услови", "ветвлен", "if", "логическ"),
                 ("цикл", "for", "while", "повторен"),
             )),
        ],
    ),
    Case(
        num=2,
        name="«Искусственный интеллект в работе учителя»",
        text=(
            "Программа повышения квалификации «Искусственный интеллект в работе учителя» для "
            "учителей школ Ингушетии, 36 часов, очно-заочно с применением дистанционных "
            "технологий. Учителя должны научиться готовить с помощью нейросетей планы уроков, "
            "задания и проверочные работы, а также понимать риски и правила безопасного "
            "использования ИИ в работе с детьми. Итоговая аттестация в форме зачёта по "
            "портфолио из трёх материалов, созданных с помощью ИИ. Использовать только "
            "сервисы, которые доступны в России."
        ),
        answers={
            "entry_level": "без навыков программирования",
            "audience": "учителя школ Ингушетии",
        },
        checks=[
            ("выбран шаблон повышения квалификации", template_is(ProgramType.DPP_PK)),
            ("объём ровно 36 ч", hours_eq(36)),
            ("компетенции связаны с профстандартом", prof_standards(True)),
            content_check("выдаётся удостоверение о ПК", issued_document_mentions("удостоверение")),
            content_check("есть тема о рисках и этике",
             mentions("этик", "риск", "персональн", what="этика и риски")),
            content_check("аттестация — зачёт по портфолио", attestation_is("портфолио", "зач")),
            content_check("задания на реальных задачах учителя",
             mentions("урок", "задани", "проверочн", what="задачи учителя")),
        ],
    ),
    Case(
        num=3,
        name="«Анализ данных на Python» для сотрудников банков",
        text=(
            "Нужна программа повышения квалификации на 72 часа «Анализ данных на Python» для "
            "сотрудников региональных отделений банков, которые сейчас работают в Excel. "
            "Формат очно-заочный, срок 4 недели. Основные темы это pandas, визуализация, "
            "SQL для выгрузки данных и основы статистики. Итоговая аттестация в виде защиты "
            "проекта на обезличенных банковских данных. Прикладываю нашу прошлую программу, "
            "структуру можно взять оттуда, но содержание нужно новое. Слайды нужны для "
            "модуля про pandas."
        ),
        answers={
            "entry_level": "уверенная работа в Excel",
            "audience": "сотрудники региональных отделений банков",
        },
        checks=[
            ("выбран шаблон повышения квалификации", template_is(ProgramType.DPP_PK)),
            ("объём ровно 72 ч", hours_eq(72)),
            ("график на 4 недели", weeks_eq(4)),
            content_check("четыре заявленные темы раскрыты",
             covers_topics(("pandas",), ("визуализац", "график"), ("sql",), ("статистик",))),
            content_check("есть вводный блок по основам Python",
             mentions("основы python", "введение в python", "базов", what="вводный блок")),
            content_check("итоговый проект на обезличенных данных",
             mentions("обезличен", "проект", what="итоговый проект")),
        ],
    ),
    Case(
        num=4,
        name="Курс по кибербезопасности для колледжа",
        text=(
            "Сделайте курс по кибербезопасности для студентов колледжа, примерно на месяц, "
            "чтобы после него ребята могли пойти работать в эту сферу."
        ),
        # Ни типа, ни объёма, ни формы — агент обязан спросить.
        expect_questions=True,
        answers={
            "program_type": "повышение квалификации",
            "total_hours": "72",
            "audience": "студенты колледжа",
            "entry_level": "базовая компьютерная грамотность",
            "study_form": "очно-заочная",
            "attestation_form": "защита проекта",
        },
        checks=[
            ("объём взят из ответа методиста, а не выдуман", hours_eq(72)),
            content_check("результаты обучения реалистичны для 72 ч", no_overpromise()),
            ("результатов обучения разумное число", outcomes_count_between(3, 14)),
            content_check("аттестация с критериями", attestation_has_criteria(1)),
        ],
    ),
    Case(
        num=5,
        name="Стажировка «DevOps-инженер от Linux до CI/CD»",
        text=(
            "Нужна программа повышения квалификации в форме стажировки «DevOps-инженер от "
            "Linux до CI/CD» на 256 часов. Слушатели проходят стажировку в ИТ-компаниях "
            "региона, вся работа самостоятельная под руководством наставника от компании, "
            "аудиторных занятий нет. Итоговая аттестация занимает 2 часа и проходит в форме "
            "защиты отчёта о стажировке. Срок около двух месяцев, не больше 4 часов в день."
        ),
        # Ответы методиста на всё, что модель может не извлечь из текста.
        answers={
            "entry_level": "базовые знания Linux",
            "audience": "слушатели стажировки в ИТ-компаниях региона",
            "study_form": "заочная",
        },
        checks=[
            ("выбран шаблон повышения квалификации", template_is(ProgramType.DPP_PK)),
            ("объём ровно 256 ч", hours_eq(256)),
            ("аудиторных часов нет", no_contact_hours()),
            ("аттестация занимает 2 ч", attestation_hours_eq(2)),
            ("254 ч самостоятельной работы", self_study_eq(254)),
            ("срок около двух месяцев", months_about(2)),
            ("нагрузка не больше 4 ч в день", daily_load_at_most(4)),
            content_check("темы покрывают стек DevOps",
             covers_topics(("linux",), ("сет",), ("контейнер", "docker"),
                           ("ci/cd", "ci", "непрерывн"), ("мониторинг",))),
            content_check("отчёт о стажировке с критериями",
             mentions("отчёт", "отчет", what="отчёт о стажировке")),
        ],
    ),
]

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
OFF = "\033[0m"


async def run_case(case: Case, provider) -> tuple[int, int]:  # noqa: ANN001
    print(f"\n{'=' * 72}\nЗапрос {case.num}. {case.name}\n{'=' * 72}")

    intake, _ = await run_intake(provider, case.text)
    asked = [q.field for q in intake.questions]
    print(f"{DIM}intake: не хватает {asked or '—'}{OFF}")

    if case.expect_questions and not asked:
        print(f"{RED}FAIL{OFF}  агент не задал уточняющих вопросов, хотя в запросе нет ни типа, ни объёма")
        return 0, 1
    if case.expect_questions:
        print(f"{GREEN} ok {OFF}  агент задал вопросы, а не домыслил параметры")

    request = intake.request
    if case.answers:
        unparsed = apply_answers(request, case.answers)
        if unparsed:
            print(f"{RED}FAIL{OFF}  не разобраны ответы методиста: {unparsed}")

    missing = request.missing_fields()
    if missing:
        print(f"{RED}FAIL{OFF}  после ответов всё ещё не хватает: {missing} — каркас не собрать")
        return 0, 1

    program, _ = await build_skeleton(provider, request)
    await build_content(provider, program, test_total=case.tests)

    report = validate_program(program)
    offline = provider.name.startswith("mock")
    passed = failed = skipped = 0
    for title, check in (*case.checks, *COMMON):
        if offline and title in NEEDS_MODEL:
            # Заглушка пишет болванки: проверять по ним смысл бессмысленно.
            print(f"{DIM}  —   {title} (нужна живая модель){OFF}")
            skipped += 1
            continue
        ok, detail = check(program)
        mark = f"{GREEN} ok {OFF}" if ok else f"{RED}FAIL{OFF}"
        print(f"{mark}  {title}{DIM} — {detail}{OFF}")
        passed += ok
        failed += not ok

    codes = sorted({f.code for f in report.errors})
    warns = sorted({f.code for f in report.warnings})
    print(f"{DIM}автопроверка: ошибки {codes or '—'}; предупреждения {warns or '—'}{OFF}")
    return passed, failed


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only", type=int, choices=[c.num for c in CASES], help="прогнать один запрос")
    args = p.parse_args()

    provider = build_provider()
    cases = [c for c in CASES if args.only is None or c.num == args.only]
    print(f"провайдер: {provider.name}; запросов: {len(cases)}")

    total_ok = total_bad = 0
    for case in cases:
        # Сбой на одном запросе не должен обрывать прогон: остальные четыре
        # всё равно надо увидеть, иначе одна ошибка модели скрывает картину.
        try:
            ok, bad = await run_case(case, provider)
        except Exception as exc:  # noqa: BLE001 — показываем и идём дальше
            print(f"{RED}СБОЙ{OFF}  запрос {case.num} упал: {exc}")
            ok, bad = 0, 1
        total_ok += ok
        total_bad += bad

    print(f"\n{'=' * 72}")
    verdict = f"{GREEN}всё прошло{OFF}" if not total_bad else f"{RED}провалов: {total_bad}{OFF}"
    print(f"итог: пройдено {total_ok}, {verdict}")
    return 1 if total_bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
