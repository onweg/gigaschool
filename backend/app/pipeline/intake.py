"""Шаг 1: разбор запроса и уточняющие вопросы.

Человек в контуре: без ответов на критичные вопросы агент не идёт дальше.
Вопросов не больше MAX_QUESTIONS — требование эталонного чек-листа кейса.
"""
from __future__ import annotations

import re

from app.llm.base import LLMProvider, Usage
from app.schemas.ir import ProgramType, StudyForm
from app.schemas.request import ClarifyingQuestion, IntakeResult, ProgramRequest
from app.skills import load_skill

MAX_QUESTIONS = 5

#: Человекочитаемые формулировки вопросов — чтобы не зависеть от модели в том,
#: что проверяет чек-лист («вопросы понятны методисту, без жаргона»).
QUESTION_TEXT: dict[str, str] = {
    "program_type": (
        "Какой это тип программы: повышение квалификации (с удостоверением) "
        "или общеразвивающая программа?"
    ),
    "total_hours": "Какой объём программы в академических часах?",
    "audience": "Кто слушатели: должность, уровень образования, организация?",
    "entry_level": "Какой у слушателей входной уровень подготовки?",
    "study_form": "Форма обучения: очная, очно-заочная или заочная?",
    "attestation_form": (
        "В какой форме проходит итоговая аттестация: экзамен, зачёт, защита проекта?"
    ),
}

_WEEKS = re.compile(r"(\d+)\s*недел", re.IGNORECASE)
_DAYS = re.compile(r"(\d+)\s*(?:дн|день|дней)", re.IGNORECASE)
_PER_DAY = re.compile(r"(?:не\s*больше|до)\s*(\d+)\s*час\w*\s*в\s*день", re.IGNORECASE)
_NUM_WORDS = {
    "один": 1, "одно": 1, "раз": 1, "два": 2, "дважды": 2, "двух": 2, "три": 3,
    "трижды": 3, "трёх": 3, "трех": 3, "четыре": 4, "пять": 5, "шесть": 6,
}
_NUM = r"(\d+|" + "|".join(_NUM_WORDS) + r")"
_SESSIONS = re.compile(
    rf"по\s*{_NUM}\s*час\w*\s*{_NUM}\s*раз\w*\s*в\s*недел", re.IGNORECASE
)
_MONTHS = re.compile(r"(\d+)\s*месяц", re.IGNORECASE)


def _as_number(token: str) -> int:
    token = token.lower()
    return int(token) if token.isdigit() else _NUM_WORDS.get(token, 1)


def parse_schedule_hints(text: str, request: ProgramRequest) -> None:
    """Расписание вытаскиваем регуляркой, а не моделью: это арифметика."""
    if m := _WEEKS.search(text):
        request.weeks = int(m.group(1))
    if m := _MONTHS.search(text):
        request.weeks = request.weeks or int(m.group(1)) * 4
    if m := _DAYS.search(text):
        request.days = int(m.group(1))
    if m := _PER_DAY.search(text):
        request.max_hours_per_day = float(m.group(1))
    if m := _SESSIONS.search(text):
        request.hours_per_session = float(_as_number(m.group(1)))
        per_week = int(_as_number(m.group(2)))
        if request.weeks:
            request.sessions = per_week * request.weeks
        elif request.total_hours and request.hours_per_session:
            request.sessions = int(request.total_hours / request.hours_per_session)
            request.weeks = max(1, round(request.sessions / per_week))
    if request.days is None and request.weeks:
        request.days = request.weeks * 7
    if "не меньше половины" in text.lower() and "практик" in text.lower():
        request.min_practice_share = 0.5


async def run_intake(
    provider: LLMProvider,
    request_text: str,
    *,
    references: list[str] | None = None,
) -> tuple[IntakeResult, Usage]:
    skill = load_skill("intake")
    references_block = ""
    if references:
        references_block = "Референсы (структуру можно использовать, текст — нет):\n" + "\n---\n".join(
            ref[:4000] for ref in references
        )
    data, usage = await provider.complete_json(
        system=skill.system,
        user=skill.render_user(
            request_text=request_text, references_block=references_block
        ),
    )

    request = ProgramRequest(raw_text=request_text)
    request.title = data.get("title") or None
    if pt := data.get("program_type"):
        try:
            request.program_type = ProgramType(pt)
        except ValueError:
            request.program_type = None
    request.program_type_confidence = float(data.get("program_type_confidence") or 0)
    request.direction = data.get("direction") or ""
    if hours := data.get("total_hours"):
        request.total_hours = float(hours)
    request.audience = data.get("audience") or ""
    request.entry_level = data.get("entry_level") or ""
    if sf := data.get("study_form"):
        try:
            request.study_form = StudyForm(sf)
        except ValueError:
            request.study_form = None
    request.uses_dot = bool(data.get("uses_dot", True))
    request.attestation_form = data.get("attestation_form") or None
    request.duration_note = data.get("duration_note") or ""
    if mhd := data.get("max_hours_per_day"):
        request.max_hours_per_day = float(mhd)
    if mps := data.get("min_practice_share"):
        request.min_practice_share = float(mps)
    request.no_contact_hours = bool(data.get("no_contact_hours", False))
    request.constraints = [str(c) for c in data.get("constraints") or []]
    request.deliverables = [str(d) for d in data.get("deliverables") or []]

    parse_schedule_hints(request_text, request)

    # Вопросы формируем сами по списку недостающих полей: так гарантируется
    # и лимит, и формулировки, понятные методисту.
    questions = [
        ClarifyingQuestion(field=f, question=QUESTION_TEXT[f])
        for f in request.missing_fields()
        if f in QUESTION_TEXT
    ][:MAX_QUESTIONS]

    notes: list[str] = []
    if request.program_type and request.program_type_confidence < 0.6:
        notes.append(
            f"Тип программы определён предположительно как "
            f"{request.program_type.value} — подтвердите."
        )
    return IntakeResult(request=request, questions=questions, notes=notes), usage


def apply_answers(request: ProgramRequest, answers: dict[str, str]) -> list[str]:
    """Применить ответы методиста. Возвращает список непонятых ответов."""
    failed: list[str] = []
    for field, value in answers.items():
        value = (value or "").strip()
        if not value:
            continue
        low = value.lower()
        if field == "program_type":
            if "общеразв" in low or "школьн" in low or "dop" in low:
                request.program_type = ProgramType.DOP_OBSH
            elif "повыш" in low or "квалиф" in low or "pk" in low:
                request.program_type = ProgramType.DPP_PK
            else:
                failed.append(field)
                continue
            request.program_type_confidence = 1.0
        elif field == "total_hours":
            if m := re.search(r"(\d+(?:[.,]\d+)?)", value):
                request.total_hours = float(m.group(1).replace(",", "."))
            else:
                failed.append(field)
        elif field == "study_form":
            if "очно-заоч" in low or "очно заоч" in low:
                request.study_form = StudyForm.PART_TIME
            elif "заоч" in low:
                request.study_form = StudyForm.DISTANT
            elif "очн" in low:
                request.study_form = StudyForm.FULL_TIME
            else:
                failed.append(field)
        elif field == "audience":
            request.audience = value
        elif field == "entry_level":
            request.entry_level = value
        elif field == "attestation_form":
            request.attestation_form = value
        else:
            request.constraints.append(f"{field}: {value}")
    parse_schedule_hints(" ".join(answers.values()), request)
    return failed


#: Примеры допустимых ответов. Переспрашивать «тем же вопросом» бесполезно:
#: методист уже ответил как умел, значит нужен образец формата.
REASK_HINTS: dict[str, str] = {
    "program_type": (
        "Не понял тип программы. Напишите одним из вариантов: "
        "«повышение квалификации» или «общеразвивающая»."
    ),
    "total_hours": "Не понял объём. Напишите числом, например: «72» или «72 часа».",
    "study_form": (
        "Не понял форму обучения. Варианты: «очная», «очно-заочная», «заочная»."
    ),
    "audience": "Для кого программа? Например: «сотрудники банков» или «школьники 8–9 классов».",
    "entry_level": "Какой входной уровень? Например: «без опыта» или «базовые навыки Python».",
    "attestation_form": (
        "Чем заканчивается обучение? Например: «защита проекта», «тестирование», «экзамен»."
    ),
}


def reask_text(field: str) -> str:
    """Текст повторного вопроса по полю, которое не удалось распознать."""
    return REASK_HINTS.get(field, f"Уточните, пожалуйста, значение для «{field}».")
