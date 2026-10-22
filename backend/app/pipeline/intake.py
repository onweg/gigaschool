"""Шаг 1: разбор запроса и уточняющие вопросы.

Человек в контуре: без ответов на критичные вопросы агент не идёт дальше.
Вопросов не больше MAX_QUESTIONS — требование эталонного чек-листа кейса.
"""
from __future__ import annotations

import re

from app.llm.base import LLMProvider, Usage, as_number
from app.schemas.ir import ProgramType, StudyForm
from app.schemas.request import ClarifyingQuestion, IntakeResult, ProgramRequest
from app.config import get_settings
from app.skills import load_skill

#: Предел уточняющих вопросов за раз — из настроек, по умолчанию пять,
#: как требует эталонный чек-лист кейса.
MAX_QUESTIONS = get_settings().max_clarifying_questions

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
#: Срок словами встречается не реже, чем цифрой: «около двух месяцев».
_MONTHS = re.compile(rf"{_NUM}\s*месяц", re.IGNORECASE)
#: Объём программы. Кандидатов может быть несколько («16 часов», «по 2 часа
#: два раза в неделю», «не больше 4 часов в день»), поэтому отбрасываем те,
#: что стоят после «по», «до», «не больше» — это нагрузка занятия, не объём.
_HOURS = re.compile(
    r"(по|до|не\s*больше|не\s*менее|каждые)?\s*(\d{2,4})\s*(?:академическ\w*\s*)?час",
    re.IGNORECASE,
)
#: Тип программы прямо назван в запросе методиста почти всегда.
_TYPE_OBSH = re.compile(r"общеразвивающ|общеобразовательн", re.IGNORECASE)
_TYPE_PK = re.compile(r"повышени\w*\s+квалификации", re.IGNORECASE)
#: Форма обучения — закрытый список из трёх значений, её незачем угадывать
#: моделью: в запросе методиста она почти всегда названа прямо. Порядок
#: важен — «очно-заочная» содержит в себе и «очно», и «заочно».
_FORMS = (
    (re.compile(r"очно[\s-]*заочн", re.IGNORECASE), StudyForm.PART_TIME),
    (re.compile(r"дистанционн\w*\s+формат|полностью\s+дистанционн|заочн", re.IGNORECASE), StudyForm.DISTANT),
    (re.compile(r"\bочн", re.IGNORECASE), StudyForm.FULL_TIME),
)
#: «Аудиторных занятий нет» — это не мнение, а структура часов, поэтому ловим
#: текстом, а не доверяем модели: от флага зависит всё распределение по видам
#: работы (стажировка из кейса — 256 ч целиком самостоятельной работы).
_NO_CONTACT = re.compile(
    r"аудиторн\w*(?:\s+\w+){0,2}\s*(?:нет|не\s+предусмотрен\w*|отсутству\w*)"
    r"|без\s+аудиторн\w*"
    r"|(?:вся|все|всё)\s+(?:работа\s+)?самостоятельн\w*",
    re.IGNORECASE,
)


def _as_number(token: str) -> int:
    token = token.lower()
    return int(token) if token.isdigit() else _NUM_WORDS.get(token, 1)


_WORD = re.compile(r"[a-zа-яё0-9+#]+")


def _grounded(value: str, text: str) -> bool:
    """Есть ли у значения опора в тексте запроса.

    Сверяем по основам слов: «защита проекта» опирается на «защищает
    проект», «работа в Excel» — на «работают в Excel». А «начальный уровень»
    в запросе «программа по Питону для студентов» опоры не имеет — это
    догадка модели, и её надо спросить у методиста.
    """
    haystack = text.lower().replace("ё", "е")
    words = [w for w in _WORD.findall(value.lower().replace("ё", "е")) if len(w) >= 4 or w.isdigit()]
    if not words:
        return value.strip().lower() in haystack
    return any(w[:5] in haystack for w in words)


#: Поля, которые модель склонна додумывать, хотя промпт это запрещает.
#: Живой случай 23 октября: «Программа по Питону на 200 часов для студентов» —
#: модель сама поставила входной уровень и форму аттестации, и вместо пяти
#: уточнений агент задал два.
_GROUNDED_FIELDS = ("audience", "entry_level", "attestation_form")


_AUDIENCE = re.compile(
    r"\bдля\s+([^,.;:!?«»()]+?)(?=\s+(?:без|с|со|которые|кто|на|по|в)\b|[,.;:!?()]|$)",
    re.IGNORECASE,
)
_ENTRY = re.compile(
    r"(без\s+(?:опыта|навыков|знаний)[^,.;:!?]*"
    r"|с\s+нуля"
    r"|(?:которые|кто)\s+(?:сейчас\s+|уже\s+)?(?:работают|владеют|знают|умеют)[^,.;:!?]*)",
    re.IGNORECASE,
)
_ATTESTATION = (
    (re.compile(r"защи\w*\s+(?:\w+\s+){0,2}?отч[её]т", re.I), "защита отчёта"),
    (re.compile(r"защи\w*\s+(?:\w+\s+){0,2}?проект", re.I), "защита проекта"),
    (re.compile(r"зач[её]т\w*\s+по\s+портфолио", re.I), "зачёт по портфолио"),
    (re.compile(r"\bзач[её]т", re.I), "зачёт"),
    (re.compile(r"\bэкзамен", re.I), "экзамен"),
)


def fill_from_text(request: ProgramRequest, text: str) -> None:
    """Аудитория, входной уровень и форма аттестации, названные прямым текстом.

    Подстраховка на случай, когда модель их не вернула: что методист написал
    сам, переспрашивать не нужно. Чего в тексте нет — останется пустым, и
    агент спросит. Значение модели, если оно есть, не трогаем.
    """
    if not request.audience and (m := _AUDIENCE.search(text)):
        request.audience = m.group(1).strip()
    if not request.entry_level and (m := _ENTRY.search(text)):
        request.entry_level = m.group(1).strip()
    if not request.attestation_form:
        for pattern, form in _ATTESTATION:
            if pattern.search(text):
                request.attestation_form = form
                break


def drop_ungrounded(request: ProgramRequest, text: str) -> list[str]:
    """Убрать значения, которых в запросе нет. Возвращает снятые поля."""
    dropped: list[str] = []
    for field in _GROUNDED_FIELDS:
        value = getattr(request, field) or ""
        if value and not _grounded(value, text):
            setattr(request, field, "" if field != "attestation_form" else None)
            dropped.append(field)
    # Объём: число из ответа модели должно встречаться в тексте. «Примерно
    # месяц» — не 72 часа, сколько бы модель ни насчитала.
    if request.total_hours and not re.search(
        rf"(?<![\d.,]){request.total_hours:g}(?![\d])", text
    ):
        request.total_hours = None
        dropped.append("total_hours")
    return dropped


def parse_schedule_hints(text: str, request: ProgramRequest) -> None:
    """Расписание вытаскиваем регуляркой, а не моделью: это арифметика.

    Порядок важен: число недель выводится из объёма программы и длительности
    занятия, поэтому объём разбирается первым.
    """
    if request.total_hours is None:
        for prefix, value in _HOURS.findall(text):
            if prefix:
                continue  # «по 2 часа», «не больше 4 часов в день» — не объём
            hours = float(value)
            # Программ короче 16 ак. ч. в кейсе нет; меньшие числа — это
            # длительность занятия или срок, а не объём программы.
            if hours >= 16:
                request.total_hours = hours
                break
    if request.program_type is None:
        if _TYPE_OBSH.search(text):
            request.program_type = ProgramType.DOP_OBSH
            request.program_type_confidence = max(request.program_type_confidence, 0.9)
        elif _TYPE_PK.search(text):
            request.program_type = ProgramType.DPP_PK
            request.program_type_confidence = max(request.program_type_confidence, 0.9)
    if m := _WEEKS.search(text):
        request.weeks = int(m.group(1))
    if m := _MONTHS.search(text):
        request.weeks = request.weeks or _as_number(m.group(1)) * 4
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
    if _NO_CONTACT.search(text):
        request.no_contact_hours = True
    if request.study_form is None:
        for pattern, form in _FORMS:
            if pattern.search(text):
                request.study_form = form
                break


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
    request.program_type_confidence = as_number(data.get("program_type_confidence"), 0.0)
    request.direction = data.get("direction") or ""
    if hours := as_number(data.get("total_hours")):
        request.total_hours = hours
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
    if mhd := as_number(data.get("max_hours_per_day")):
        request.max_hours_per_day = mhd
    if mps := as_number(data.get("min_practice_share")):
        request.min_practice_share = mps / 100 if mps > 1 else mps  # «50» — это проценты
    request.no_contact_hours = bool(data.get("no_contact_hours", False))
    request.constraints = [str(c) for c in data.get("constraints") or []]
    request.must_cover = [
        str(x).strip() for x in data.get("must_cover") or [] if str(x).strip()
    ]
    request.deliverables = [str(d) for d in data.get("deliverables") or []]

    dropped = drop_ungrounded(request, " ".join([request_text, *(references or [])]))
    fill_from_text(request, request_text)

    parse_schedule_hints(request_text, request)

    # Форма обучения следует из структуры часов. «Очно-заочная» при нуле
    # аудиторных часов — противоречие в документе, поэтому мнение модели здесь
    # не решает: нет аудиторных занятий, значит заочная. Заодно это снимает
    # лишний вопрос методисту, который уже написал «вся работа самостоятельная».
    if request.no_contact_hours:
        request.study_form = StudyForm.DISTANT

    # Вопросы формируем сами по списку недостающих полей: так гарантируется
    # и лимит, и формулировки, понятные методисту.
    questions = [
        ClarifyingQuestion(field=f, question=QUESTION_TEXT[f])
        for f in request.missing_fields()
        if f in QUESTION_TEXT
    ][:MAX_QUESTIONS]

    notes: list[str] = []
    if dropped:
        notes.append(
            "Модель предложила значения, которых нет в запросе ("
            + ", ".join(dropped) + ") — не беру их, уточняю у методиста."
        )
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
