"""Автопроверка комплекта — то, по чему АЦР принимает решение.

Каждая проверка воспроизводит пункт эталонного чек-листа кейса. Отчёт
отдаётся методисту и одновременно служит внутренним гейтом: при ERROR
агент не переходит к генерации содержания.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.ir import (
    HOUR_STEP,
    AssessmentKind,
    Hours,
    Program,
    ProgramType,
    VerificationStatus,
)

EPS = 1e-6


class Severity(str, Enum):
    ERROR = "error"  # нарушен инвариант чек-листа — программа вернётся на доработку
    WARNING = "warning"  # требует внимания методиста
    INFO = "info"


class Finding(BaseModel):
    code: str
    severity: Severity
    message: str
    path: str = ""  # «modules[1].topics[2].hours»
    hint: str = ""


class ValidationReport(BaseModel):
    findings: list[Finding] = Field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, code: str, severity: Severity, message: str, **kw: str) -> None:
        self.findings.append(
            Finding(code=code, severity=severity, message=message, **kw)
        )

    def summary(self) -> dict:
        return {
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "checks": len(self.findings),
        }


# --------------------------------------------------------------------------- #
# 1. Арифметика часов
# --------------------------------------------------------------------------- #
def check_hours(program: Program, report: ValidationReport) -> None:
    kinds = ("lectures", "practice", "labs", "self_study")

    for mi, module in enumerate(program.modules):
        if not module.topics:
            report.add(
                "HOURS_NO_TOPICS",
                Severity.WARNING,
                f"Модуль «{module.title}» без тем — часы нечем подтвердить",
                path=f"modules[{mi}]",
                hint="Добавить в модуль хотя бы одну тему или удалить пустой модуль",
            )
            continue
        for kind, (got, want) in module.hours_gaps().items():
            report.add(
                "HOURS_TOPIC_SUM",
                Severity.ERROR,
                f"Модуль {module.index} «{module.title}»: сумма тем по «{kind}» "
                f"= {got} ч, в строке модуля {want} ч",
                path=f"modules[{mi}].hours.{kind}",
                hint="Пересобрать часы тем солвером (core.hours.rebalance_topics)",
            )

    for mi, module in enumerate(program.modules):
        if abs(module.hours.total - sum(
            getattr(module.hours, k) for k in kinds
        )) > EPS:
            report.add(
                "HOURS_ROW_SUM",
                Severity.ERROR,
                f"Модуль {module.index}: ЛК+ПР+ЛБ+СРС не равно трудоёмкости строки",
                path=f"modules[{mi}].hours",
                hint="Исправить часы в строке: сумма по видам работы должна равняться графе «Всего»",
            )

    balance = program.hours_check
    grand = balance["total"]
    if abs(balance["gap"]) > EPS:
        report.add(
            "HOURS_GRAND_TOTAL",
            Severity.ERROR,
            f"Итого {grand} ч (модули {program.modules_hours.total} + "
            f"аттестация {program.final_attestation.hours}) ≠ запрошенных "
            f"{program.meta.total_hours} ч",
            path="meta.total_hours",
            hint="Привести итог к заказанному объёму: поправить часы модуля или итоговой аттестации",
        )

    for mi, module in enumerate(program.modules):
        for ti, topic in enumerate(module.topics):
            for kind in kinds:
                val = getattr(topic.hours, kind)
                if val < 0:
                    report.add(
                        "HOURS_NEGATIVE",
                        Severity.ERROR,
                        f"Отрицательные часы в теме {topic.code}",
                        path=f"modules[{mi}].topics[{ti}].hours.{kind}",
                        hint="Отрицательных часов быть не может — задать значение заново",
                    )
                if abs(val / HOUR_STEP - round(val / HOUR_STEP)) > EPS:
                    report.add(
                        "HOURS_GRAIN",
                        Severity.WARNING,
                        f"Тема {topic.code}: {val} ч не кратно {HOUR_STEP} ч",
                        path=f"modules[{mi}].topics[{ti}].hours.{kind}",
                        hint=f"Округлить до {HOUR_STEP} ч: дробные часы "
                             f"в учебном плане не ставят",
                    )

    # Форма аттестации без критериев — половина требования: методист не
    # сможет ни принять работу, ни объяснить слушателю, что от него хотят.
    if program.final_attestation.form and not program.final_attestation.criteria:
        report.add(
            "ATTESTATION_NO_CRITERIA",
            Severity.WARNING,
            f"У итоговой аттестации «{program.final_attestation.form}» "
            f"не заданы критерии оценки",
            path="final_attestation.criteria",
            hint="Добавить 3-5 критериев: по ним принимают работу и объясняют "
                 "слушателю требования",
        )
    if program.final_attestation.hours <= 0:
        report.add(
            "ATTESTATION_HOURS",
            Severity.ERROR,
            "Итоговой аттестации не выделены часы",
            path="final_attestation.hours",
            hint="Выделить часы на аттестацию, забрав их у модулей: "
                 "итог программы должен остаться прежним",
        )


# --------------------------------------------------------------------------- #
# 2. Покрытие результатов обучения
# --------------------------------------------------------------------------- #
#: Требование кейса, а не настройка: общие требования к эталонным
#: чек-листам (data/acr/case-open-requests.docx) — «каждый результат
#: обучения ... проверяется хотя бы двумя оценочными средствами».
MIN_ASSESSMENTS_PER_OUTCOME = 2


def check_coverage(program: Program, report: ValidationReport) -> None:
    if not program.outcomes:
        report.add(
            "COVERAGE_NO_OUTCOMES",
            Severity.ERROR,
            "Не заданы результаты обучения",
            path="outcomes",
            hint="Добавить планируемые результаты обучения: без них программу не утвердят",
        )
        return

    topics_by_outcome: dict[str, list[str]] = {o.id: [] for o in program.outcomes}
    for topic in program.all_topics():
        for oid in topic.outcome_ids:
            topics_by_outcome.setdefault(oid, []).append(topic.code)

    # Ссылки на несуществующие РО здесь не считаем — их отдельно ловит
    # ASSESSMENT_UNKNOWN_OUTCOME, иначе такой РО-призрак дал бы ещё и
    # ложное «проверяется менее чем двумя средствами».
    assess_by_outcome: dict[str, set[str]] = {o.id: set() for o in program.outcomes}
    for q in program.test_questions:
        for oid in q.outcome_ids:
            if oid in assess_by_outcome:
                assess_by_outcome[oid].add(AssessmentKind.TEST.value)
    for t in program.seminar_tasks:
        for oid in t.outcome_ids:
            if oid in assess_by_outcome:
                assess_by_outcome[oid].add(AssessmentKind.SEMINAR.value)
    # Итоговая аттестация покрывает все РО, если так заявлено
    if program.final_attestation.form:
        for oid in assess_by_outcome:
            assess_by_outcome[oid].add("final")

    known = {o.id for o in program.outcomes}
    for oid, codes in topics_by_outcome.items():
        if oid not in known:
            report.add(
                "COVERAGE_UNKNOWN_OUTCOME",
                Severity.ERROR,
                f"Темы ссылаются на неизвестный результат обучения {oid}",
                hint="Добавить этот результат обучения в раздел РО "
                     "или исправить ссылку в теме",
            )
            continue
        if not codes:
            report.add(
                "COVERAGE_OUTCOME_NO_TOPIC",
                Severity.ERROR,
                f"Результат обучения {oid} не раскрыт ни в одной теме",
                path="outcomes",
                hint="Привязать РО к теме или удалить РО",
            )

    for oid, kinds in assess_by_outcome.items():
        if len(kinds) < MIN_ASSESSMENTS_PER_OUTCOME:
            report.add(
                "COVERAGE_OUTCOME_ASSESSMENT",
                Severity.WARNING if not program.test_questions else Severity.ERROR,
                f"Результат обучения {oid} проверяется менее чем "
                f"{MIN_ASSESSMENTS_PER_OUTCOME} оценочными средствами "
                f"(сейчас: {', '.join(sorted(kinds)) or 'нет'})",
                path="test_questions/seminar_tasks",
                hint="Добавить тестовый вопрос или задание семинара на этот результат обучения",
            )

    for topic in program.all_topics():
        if not topic.outcome_ids:
            report.add(
                "COVERAGE_TOPIC_NO_OUTCOME",
                Severity.WARNING,
                f"Тема {topic.code} «{topic.title}» не привязана ни к одному РО",
                hint="Привязать тему к результату обучения или убрать тему из плана",
            )


# --------------------------------------------------------------------------- #
# 2а. Связи компетенция ↔ результат обучения ↔ оценочное средство
# --------------------------------------------------------------------------- #
def check_links(program: Program, report: ValidationReport) -> None:
    """Сквозная трассировка: компетенция раскрывается результатами обучения,
    а те проверяются оценочными средствами. Обрыв в любом звене означает,
    что компетенцию заявили, но не учат или не проверяют.

    Разбор прогона 20 октября: «недостатки в покрытии сложных сценариев
    связей компетенций и оценочных средств». Сценарии ниже — именно такие:
    ссылки в никуда, расхождение двух сторон связи, компетенция, которую
    проверяет только итоговая аттестация.
    """
    outcomes = {o.id: o for o in program.outcomes}
    competencies = {c.id: c for c in program.competencies}
    standards = {s.code for s in program.prof_standards}
    owned: dict[str, str] = {}

    for ci, comp in enumerate(program.competencies):
        path = f"competencies[{ci}]"
        unknown = [oid for oid in comp.outcome_ids if oid not in outcomes]
        if unknown:
            report.add(
                "COMPETENCY_UNKNOWN_OUTCOME",
                Severity.ERROR,
                f"Компетенция {comp.id} ссылается на несуществующие РО: {', '.join(unknown)}",
                path=f"{path}.outcome_ids",
                hint="Убрать ссылку или добавить этот результат обучения в раздел РО",
            )
        known = [oid for oid in comp.outcome_ids if oid in outcomes]
        if not known:
            report.add(
                "COMPETENCY_NO_OUTCOMES",
                Severity.ERROR,
                f"Компетенция {comp.id} «{comp.title[:60]}» не раскрыта ни одним результатом обучения",
                path=f"{path}.outcome_ids",
                hint="Привязать к компетенции знания, умения или навыки из раздела РО",
            )
        for oid in known:
            owned.setdefault(oid, comp.id)
            linked = outcomes[oid].competency_id
            if linked and linked != comp.id:
                report.add(
                    "COMPETENCY_LINK_MISMATCH",
                    Severity.WARNING,
                    f"РО {oid} указан в компетенции {comp.id}, но сам ссылается на {linked}",
                    path=f"{path}.outcome_ids",
                    hint="Выбрать одну компетенцию для результата обучения и исправить обе стороны связи",
                )
        if comp.prof_standard_code and comp.prof_standard_code not in standards:
            report.add(
                "COMPETENCY_UNKNOWN_STANDARD",
                Severity.WARNING,
                f"Компетенция {comp.id} опирается на профстандарт {comp.prof_standard_code}, "
                f"которого нет в разделе профстандартов",
                path=f"{path}.prof_standard_code",
                hint="Добавить профстандарт в программу или снять ссылку с компетенции",
            )

    for oi, outcome in enumerate(program.outcomes):
        if outcome.competency_id and outcome.competency_id not in competencies:
            report.add(
                "OUTCOME_UNKNOWN_COMPETENCY",
                Severity.ERROR,
                f"РО {outcome.id} ссылается на несуществующую компетенцию {outcome.competency_id}",
                path=f"outcomes[{oi}].competency_id",
                hint="Привязать РО к компетенции из карты компетенций",
            )
        elif program.competencies and outcome.id not in owned and not outcome.competency_id:
            report.add(
                "OUTCOME_NO_COMPETENCY",
                Severity.WARNING,
                f"РО {outcome.id} не относится ни к одной компетенции",
                path=f"outcomes[{oi}]",
                hint="Указать, какую компетенцию формирует этот результат обучения",
            )

    items = [("test_questions", q) for q in program.test_questions] + [
        ("seminar_tasks", s) for s in program.seminar_tasks
    ]
    if not items:
        return  # каркас: оценочных средств ещё нет, проверять нечего

    topic_ids = {t.id for t in program.all_topics()}
    assessed: set[str] = set()
    for section, item in items:
        unknown = [oid for oid in item.outcome_ids if oid not in outcomes]
        if unknown:
            report.add(
                "ASSESSMENT_UNKNOWN_OUTCOME",
                Severity.ERROR,
                f"{item.id} проверяет несуществующие РО: {', '.join(unknown)}",
                path=f"{section}.{item.id}",
                hint="Перепривязать оценочное средство к РО его темы",
            )
        if not item.outcome_ids:
            report.add(
                "ASSESSMENT_NO_OUTCOME",
                Severity.WARNING,
                f"{item.id} не привязан ни к одному результату обучения",
                path=f"{section}.{item.id}",
                hint="Указать, какой РО проверяет это оценочное средство",
            )
        if item.topic_id and item.topic_id not in topic_ids:
            report.add(
                "ASSESSMENT_UNKNOWN_TOPIC",
                Severity.WARNING,
                f"{item.id} относится к теме {item.topic_id}, которой нет в плане",
                path=f"{section}.{item.id}",
                hint="Перенести оценочное средство в существующую тему или удалить",
            )
        assessed.update(oid for oid in item.outcome_ids if oid in outcomes)

    # Итоговая аттестация «покрывает всё» по определению, поэтому здесь её
    # не считаем: компетенция, которую по ходу курса ничто не проверяет,
    # обнаружится только на защите — слишком поздно для слушателя.
    for ci, comp in enumerate(program.competencies):
        known = [oid for oid in comp.outcome_ids if oid in outcomes]
        if known and not assessed.intersection(known):
            report.add(
                "COMPETENCY_NOT_ASSESSED",
                Severity.ERROR,
                f"Компетенцию {comp.id} не проверяет ни один тест или семинар — только итоговая аттестация",
                path=f"competencies[{ci}]",
                hint="Добавить вопрос теста или задание семинара на один из её результатов обучения",
            )


# --------------------------------------------------------------------------- #
# 3. Достоверность источников и нормативки
# --------------------------------------------------------------------------- #
def check_sources(program: Program, report: ValidationReport) -> None:
    for ps in program.prof_standards:
        if ps.verification is VerificationStatus.REJECTED:
            report.add(
                "SOURCE_REJECTED",
                Severity.ERROR,
                f"Профстандарт {ps.code} не найден в реестре — удалить из программы",
                path="prof_standards",
                hint="Источник не найден в реестре — заменить на существующий",
            )
        elif ps.verification is VerificationStatus.UNVERIFIED:
            report.add(
                "SOURCE_UNVERIFIED",
                Severity.WARNING,
                f"Профстандарт {ps.code} «{ps.title}» не подтверждён реестром — "
                "в документе печатается с пометкой «требует проверки»",
                path="prof_standards",
                hint="Сверить код и название на profstandart.rosmintrud.ru; "
                     "если профстандарт не подходит — заменить или убрать",
            )
    for act in program.legal_acts:
        if act.verification is not VerificationStatus.VERIFIED:
            report.add(
                "LEGAL_UNVERIFIED",
                Severity.WARNING,
                f"Нормативный акт «{act.title}» не подтверждён реестром",
                path="legal_acts",
                hint="Сверить реквизиты акта с publication.pravo.gov.ru и снять пометку",
            )
    unverified = [s for s in program.sources if s.verification is not VerificationStatus.VERIFIED]
    if unverified:
        report.add(
            "SOURCES_UNVERIFIED_LIST",
            Severity.WARNING,
            f"{len(unverified)} из {len(program.sources)} источников требуют "
            "проверки методистом (помечены в документе)",
            path="sources",
            hint="Проверить выходные данные и актуальность редакции; "
                 "непроверенные пометки снять после сверки",
        )


# --------------------------------------------------------------------------- #
# 4. Полнота шаблона и требования типа программы
# --------------------------------------------------------------------------- #
REQUIRED_COMMON = {
    "meta.title": lambda p: bool(p.meta.title),
    "goal": lambda p: bool(p.goal),
    "outcomes": lambda p: bool(p.outcomes),
    "modules": lambda p: bool(p.modules),
    "final_attestation.form": lambda p: bool(p.final_attestation.form),
    "meta.audience": lambda p: bool(p.meta.audience),
    "entry_requirements": lambda p: bool(p.entry_requirements),
}

REQUIRED_PK = {
    "annotation": lambda p: bool(p.annotation),
    "prof_standards": lambda p: bool(p.prof_standards),
    "competencies": lambda p: bool(p.competencies),
    "meta.issued_document": lambda p: bool(p.meta.issued_document),
}

REQUIRED_OBSH = {
    "relevance": lambda p: bool(p.relevance),
    "meta.level": lambda p: bool(p.meta.level),
}


def check_template(program: Program, report: ValidationReport) -> None:
    checks = dict(REQUIRED_COMMON)
    checks.update(
        REQUIRED_PK if program.meta.program_type is ProgramType.DPP_PK else REQUIRED_OBSH
    )
    for path, probe in checks.items():
        if probe(program):
            continue
        if path == "prof_standards":
            # Профстандарта под направление может не найтись в реестре, а
            # выдумывать код нельзя. Приказ № 499 допускает опору на
            # квалификационные требования, поэтому это предупреждение для
            # методиста, а не стоп: иначе каркас оставался в тупике без
            # способа его исправить, а «Убрать» неподтверждённый профстандарт
            # само создавало блокирующую ошибку.
            report.add(
                "TEMPLATE_MISSING",
                Severity.WARNING,
                "Профстандарт не указан: в реестре агента нет подходящего под направление",
                path=path,
                hint="Указать профстандарт вручную по реестру profstandart.rosmintrud.ru "
                     "или опереться на квалификационные требования (приказ № 499)",
            )
            continue
        report.add(
            "TEMPLATE_MISSING",
            Severity.ERROR,
            f"Обязательный раздел шаблона не заполнен: {path}",
            path=path,
            hint="Заполнить обязательный раздел шаблона АЦР",
        )

    if program.meta.program_type is ProgramType.DOP_OBSH and program.prof_standards:
        report.add(
            "TEMPLATE_OBSH_PROFSTANDARD",
            Severity.WARNING,
            "Общеразвивающая программа не требует привязки к профстандартам — "
            "раздел лишний",
            path="prof_standards",
            hint="Убрать профстандарты: общеразвивающая программа с ними не связывается",
        )

    # Реквизиты, которые агент обязан оставить человеку
    if program.meta.order_number or program.meta.order_date:
        report.add(
            "PLACEHOLDER_FILLED",
            Severity.ERROR,
            "Реквизиты приказа должны остаться пустыми полями для человека",
            path="meta.order_number",
            hint="Очистить поле: реквизиты приказа, ФИО и подписи вносит организация",
        )


# --------------------------------------------------------------------------- #
# 5. Согласованность расписания
# --------------------------------------------------------------------------- #
def check_schedule(program: Program, report: ValidationReport) -> None:
    s = program.schedule
    # Аудиторная нагрузка расписания = контактные часы модулей + итоговая аттестация
    # (она тоже проходит на занятии).
    classroom = round(program.modules_hours.contact + program.final_attestation.hours, 1)
    if s.sessions and s.hours_per_session:
        planned = round(s.sessions * s.hours_per_session, 1)
        if classroom and abs(planned - classroom) > EPS:
            report.add(
                "SCHEDULE_SESSIONS",
                Severity.WARNING,
                f"Календарный график: {s.sessions} занятий × {s.hours_per_session} ч "
                f"= {planned} ч, аудиторная нагрузка плана {classroom} ч "
                f"(включая итоговую аттестацию)",
                path="schedule",
                hint="Согласовать календарный график с планом: изменить число "
                     "занятий, их длительность или аудиторные часы",
            )
    if s.max_hours_per_day and s.days:
        capacity = s.max_hours_per_day * s.days
        if program.meta.total_hours - capacity > EPS:
            report.add(
                "SCHEDULE_CAPACITY",
                Severity.ERROR,
                f"{program.meta.total_hours} ч не укладываются в {s.days} дней "
                f"по {s.max_hours_per_day} ч/день (ёмкость {capacity} ч)",
                path="schedule",
                hint="Согласовать график с объёмом: изменить число занятий, недель или часов в день",
            )


# --------------------------------------------------------------------------- #
# Точка входа
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Доступность инструментов
# --------------------------------------------------------------------------- #
#: Сервисы, которыми в России нельзя пользоваться напрямую, и чем их заменить.
#: Эталонный чек-лист кейса (запрос 2) требует, чтобы такие сервисы в программу
#: не попадали. Просьбы в промпте мало: модель про это забывает, а методист
#: получает программу, по которой слушатели не смогут заниматься.
UNAVAILABLE_SERVICES: dict[str, str] = {
    "chatgpt": "GigaChat или YandexGPT",
    "openai": "Cloud.ru Foundation Models",
    "midjourney": "Kandinsky или Шедеврум",
    "dall-e": "Kandinsky",
    "copilot": "GigaCode",
    "notion": "Яндекс Документы",
    "canva": "Яндекс Презентации или Р7-Офис",
    "grammarly": "Орфограммка",
    "google colab": "Jupyter или Yandex DataSphere",
    "colab": "Jupyter или Yandex DataSphere",
}


def check_services(program: Program, report: ValidationReport) -> None:
    """Инструменты, недоступные в России, в программу попадать не должны."""
    places: list[tuple[str, str]] = [("аннотация", program.annotation), ("цель", program.goal)]
    for topic in program.all_topics():
        places.append((f"тема {topic.code}", f"{topic.title} {topic.content} {topic.activities}"))
    for task in program.seminar_tasks:
        places.append((f"задание {task.id}", f"{task.title} {task.statement}"))

    # Длинные названия ищем первыми и вырезаем из текста: иначе «google colab»
    # даст два замечания — на себя и на «colab».
    ordered = sorted(UNAVAILABLE_SERVICES, key=len, reverse=True)
    reported: set[str] = set()
    for where, text in places:
        low = (text or "").lower()
        for service in ordered:
            if service not in low:
                continue
            low = low.replace(service, " ")
            if service in reported:
                continue
            reported.add(service)
            report.add(
                "SERVICE_UNAVAILABLE_RU",
                Severity.WARNING,
                f"Упомянут сервис «{service}», недоступный в России напрямую ({where})",
                path="content",
                hint=f"Заменить на {UNAVAILABLE_SERVICES[service]} "
                     f"или другой доступный аналог",
            )


CHECKS = (
    check_hours,
    check_coverage,
    check_links,
    check_sources,
    check_template,
    check_schedule,
    check_services,
)


def validate_program(program: Program, *, stage: str = "full") -> ValidationReport:
    """stage='skeleton' — не требует оценочных средств, они ещё не сгенерированы."""
    report = ValidationReport()
    for check in CHECKS:
        if stage == "skeleton" and check is check_coverage:
            check_coverage_skeleton(program, report)
            continue
        check(program, report)
    return report


def check_coverage_skeleton(program: Program, report: ValidationReport) -> None:
    """На этапе каркаса проверяем только связь РО ↔ темы."""
    if not program.outcomes:
        report.add("COVERAGE_NO_OUTCOMES", Severity.ERROR, "Не заданы РО", path="outcomes")
        return
    covered = {oid for t in program.all_topics() for oid in t.outcome_ids}
    for o in program.outcomes:
        if o.id not in covered:
            report.add(
                "COVERAGE_OUTCOME_NO_TOPIC",
                Severity.ERROR,
                f"Результат обучения {o.id} не раскрыт ни в одной теме: «{o.text[:60]}»",
                path="outcomes",
            )
    for t in program.all_topics():
        if not t.outcome_ids:
            report.add(
                "COVERAGE_TOPIC_NO_OUTCOME",
                Severity.WARNING,
                f"Тема {t.code} «{t.title}» не привязана ни к одному РО",
            )
