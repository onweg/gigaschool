"""Проверка точечной доработки темы на живом сервере.

Требование кейса (docs/case-task.md:26): «Методист может точечно поправить
отдельные части, например попросить заменить тему 2.3 или усложнить тест,
и агент пересобирает только затронутое, сохраняя согласованность остального».

В эталонных чек-листах пяти открытых запросов этого пункта нет, поэтому
`run_open_requests.py` его не ловит — нужна отдельная проверка.

    # на готовой сессии (дешевле: комплект уже собран)
    python3 scripts/check_topic_patch.py --url http://213.159.208.169:3100 --session 137b66f2d5c2

    # со сборкой новой программы
    python3 scripts/check_topic_patch.py --url http://localhost:8000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
OFF = "\033[0m"

REQUEST = (
    "программа повышения квалификации на 72 часа по анализу данных на Python "
    "для сотрудников банков, очно-заочная форма, итоговая аттестация — защита проекта"
)
#: Пожелание нарочно узкое: по тексту сразу видно, выполнено оно или нет.
INSTRUCTION = (
    "перепиши тему целиком про оконные функции SQL: OVER, PARTITION BY, "
    "ROW_NUMBER, RANK, LAG, LEAD. Больше ни о чём не пиши."
)
MARKERS = ("оконн", "over", "partition", "row_number", "rank", "lag", "lead", "sql")
NEW_TITLE = "Оконные функции SQL"


def ok(text: str) -> str:
    return f"{GREEN} ok {OFF}  {text}"


def fail(text: str) -> str:
    return f"{RED}FAIL{OFF}  {text}"


def topic_by_id(program: dict, topic_id: str) -> dict:
    return next(t for m in program["modules"] for t in m["topics"] if t["id"] == topic_id)


def hours_of(program: dict) -> float:
    total = sum(m["hours"]["total"] for m in program["modules"])
    return round(total + program["final_attestation"]["hours"], 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Точечная доработка темы")
    parser.add_argument("--url", default="http://localhost:8000", help="адрес сервиса")
    parser.add_argument("--session", help="id готовой сессии; без него собирается новая")
    parser.add_argument("--topic", help="id темы; по умолчанию первая")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    client = httpx.Client(timeout=1800)

    # На заглушке проверка бессмысленна: она отвечает одинаково на любой
    # промпт, и «пожелание не выполнено» будет ложным провалом.
    health = client.get(f"{base}/api/health").json()
    print(f"{DIM}провайдер: {health.get('llm_provider')} · {health.get('llm_model')}{OFF}")
    if health.get("llm_provider") == "mock":
        print(f"{RED}нужна живая модель{OFF}: заглушка игнорирует любые пожелания, "
              f"проверять нечего")
        return 2

    if args.session:
        state = client.get(f"{base}/api/sessions/{args.session}").json()
        session_id, program = args.session, state.get("program")
        if not program:
            print(fail(f"в сессии {session_id} нет собранной программы"))
            return 2
    else:
        print(f"{DIM}собираю программу (это долго и платно)…{OFF}", flush=True)
        body = client.post(f"{base}/api/generate", json={"text": REQUEST}).json()
        if body.get("status") != "ready":
            print(fail(f"комплект не собрался: {body.get('status')}"))
            return 2
        session_id, program = body["session_id"], body["program"]

    topic_id = args.topic or program["modules"][0]["topics"][0]["id"]
    before = topic_by_id(program, topic_id)
    neighbours_before = {
        t["id"]: t.get("content", "")
        for m in program["modules"] for t in m["topics"] if t["id"] != topic_id
    }
    hours_before = hours_of(program)
    questions_before = len(program["test_questions"])

    print(f"сессия {session_id}, тема {topic_id}")
    print(f"\n{DIM}ДО:{OFF} {before['title']}")
    print(f"{DIM}{(before.get('content') or '')[:220]}…{OFF}")
    print(f"\nпросим: {INSTRUCTION[:80]}…\n")

    response = client.post(
        f"{base}/api/sessions/{session_id}/topics/{topic_id}/regenerate",
        json={"instruction": INSTRUCTION, "title": NEW_TITLE, "regenerate_tests": True},
    )
    if response.status_code != 200:
        print(fail(f"HTTP {response.status_code}: {response.text[:300]}"))
        return 2

    program = response.json()["program"]
    after = topic_by_id(program, topic_id)
    content = (after.get("content") or "").lower()

    print(f"{DIM}ПОСЛЕ:{OFF} {after['title']}")
    print(f"{DIM}{(after.get('content') or '')[:220]}…{OFF}\n")

    failed = 0

    hits = [m for m in MARKERS if m in content]
    if hits:
        print(ok(f"пожелание выполнено — в тексте: {', '.join(hits[:5])}"))
    else:
        print(fail("пожелание проигнорировано: ни одного упоминания оконных функций"))
        failed += 1

    if after["title"] == NEW_TITLE:
        print(ok(f"тема переименована: «{after['title']}»"))
    else:
        print(fail(f"название не изменилось: «{after['title']}», просили «{NEW_TITLE}»"))
        failed += 1

    if (after.get("content") or "") != (before.get("content") or ""):
        print(ok("содержание действительно пересобрано"))
    else:
        print(fail("содержание не изменилось вовсе"))
        failed += 1

    changed = [
        tid for tid, text in neighbours_before.items()
        if topic_by_id(program, tid).get("content", "") != text
    ]
    if changed:
        print(fail(f"затронуты соседние темы: {', '.join(changed)}"))
        failed += 1
    else:
        print(ok("остальные темы не тронуты"))

    hours_after = hours_of(program)
    if hours_after == hours_before:
        print(ok(f"часы на месте: {hours_after:g} ч"))
    else:
        print(fail(f"часы поехали: было {hours_before:g}, стало {hours_after:g}"))
        failed += 1

    questions_after = len(program["test_questions"])
    print(f"{DIM}  вопросов было {questions_before}, стало {questions_after}{OFF}")

    print(f"\n{'=' * 60}")
    if failed:
        print(f"{RED}провалов: {failed}{OFF} — точечная доработка не работает как требует кейс")
    else:
        print(f"{GREEN}всё прошло{OFF} — методист управляет пересборкой темы")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
