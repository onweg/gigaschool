"""Проверка неподтверждённых источников методистом.

Агент помечает источники, которые не смог подтвердить реестром, — это
требование кейса о защите от выдумывания. Но пометка сама по себе ничего не
решает: методисту нужно быстро понять, что проверить, где, и закрыть вопрос.

Разбор прогона 20 октября: «неподтверждённые источники помечаются, но
интерфейс не даёт явного маршрута методисту для быстрой проверки таких
источников». Здесь этот маршрут: список, ссылка, где сверить, и решение —
подтвердить или убрать из программы.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from app.schemas.ir import Program, VerificationStatus

#: Где сверять каждый вид источника. Это открытые государственные реестры
#: и сводный каталог РГБ — проверять по ним можно без доступов.
PROFSTANDART_REGISTRY = "https://profstandart.rosmintrud.ru/"
LEGAL_REGISTRY = "http://publication.pravo.gov.ru/"
LIBRARY_SEARCH = "https://search.rsl.ru/ru/search#q="

KINDS = ("prof_standard", "legal_act", "source")
ACTIONS = ("confirm", "remove")


def pending(program: Program) -> list[dict]:
    """Всё, что требует проверки, — с подсказкой, где проверить."""
    items: list[dict] = []
    for std in program.prof_standards:
        if std.verification is VerificationStatus.VERIFIED:
            continue
        items.append(
            {
                "kind": "prof_standard",
                "key": std.code,
                "title": f"Профстандарт {std.code} «{std.title}»",
                "what_to_check": "код, название и действующую редакцию",
                "check_url": std.url or PROFSTANDART_REGISTRY,
            }
        )
    for act in program.legal_acts:
        if act.verification is VerificationStatus.VERIFIED:
            continue
        items.append(
            {
                "kind": "legal_act",
                "key": act.id,
                "title": f"{act.requisites} «{act.title}»".strip(),
                "what_to_check": "реквизиты и что акт не утратил силу",
                "check_url": act.url or LEGAL_REGISTRY,
            }
        )
    for source in program.sources:
        if source.verification is VerificationStatus.VERIFIED:
            continue
        items.append(
            {
                "kind": "source",
                "key": source.id,
                "title": source.citation,
                "what_to_check": "что издание существует и выходные данные верны",
                "check_url": source.url or LIBRARY_SEARCH + quote(source.citation[:120]),
            }
        )
    return items


def review(program: Program, kind: str, key: str, action: str) -> str:
    """Решение методиста по одному источнику. Возвращает текст для журнала.

    «Подтвердить» — методист сверил источник сам, пометка снимается.
    «Убрать» — источник удаляется из программы целиком: отклонённый, но
    оставленный в документе источник хуже, чем отсутствующий.
    """
    if kind not in KINDS:
        raise ValueError(f"неизвестный вид источника: {kind}")
    if action not in ACTIONS:
        raise ValueError(f"неизвестное действие: {action}")

    stamp = f"подтверждено методистом {date.today():%d.%m.%Y}"

    if kind == "prof_standard":
        std = next((s for s in program.prof_standards if s.code == key), None)
        if std is None:
            raise KeyError(f"профстандарт {key} не найден")
        if action == "confirm":
            std.verification = VerificationStatus.VERIFIED
            std.note = stamp
            return f"Профстандарт {key} подтверждён методистом"
        program.prof_standards.remove(std)
        # Компетенции не должны ссылаться на убранный профстандарт.
        for competency in program.competencies:
            if competency.prof_standard_code == key:
                competency.prof_standard_code = None
                competency.labor_function = None
        return f"Профстандарт {key} убран из программы"

    collection = program.legal_acts if kind == "legal_act" else program.sources
    item = next((x for x in collection if x.id == key), None)
    if item is None:
        raise KeyError(f"источник {key} не найден")
    if action == "confirm":
        item.verification = VerificationStatus.VERIFIED
        item.note = stamp
        return f"Источник «{_short(item)}» подтверждён методистом"
    collection.remove(item)
    return f"Источник «{_short(item)}» убран из программы"


def _short(item: object) -> str:
    text = getattr(item, "citation", "") or getattr(item, "title", "")
    return text[:60]
