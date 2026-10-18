"""Сколько одновременных запросов пускает шлюз — и как быстро отвечают модели.

Лимит площадки задан на команду и нигде не опубликован, а от него зависит
`LLM_MAX_PARALLEL`: поставишь больше — получишь 429 посреди защиты, меньше —
комплект собирается вдвое дольше. Поэтому предел измеряем, а не угадываем.

Запросы нарочно крошечные («назови одно слово», 5 токенов ответа), так что
полная проба всех моделей стоит сотых долей рубля.

    python3 scripts/probe_parallel.py                    # модели из каталога
    python3 scripts/probe_parallel.py --levels 1 2 4 8   # свои уровни
    python3 scripts/probe_parallel.py --model GigaChat/GigaChat-3-Pro
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.config import get_settings

#: Самый дешёвый осмысленный запрос: короткий промпт, короткий ответ.
PROMPT = "Ответь одним словом: да"
MAX_TOKENS = 5

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
OFF = "\033[0m"


async def one_call(client: httpx.AsyncClient, url: str, headers: dict, model: str) -> tuple[int, float, int]:
    """Один запрос. Возвращает (код ответа, секунды, токенов всего)."""
    started = time.perf_counter()
    try:
        response = await client.post(
            url,
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": PROMPT}],
                "max_tokens": MAX_TOKENS,
                "temperature": 0,
            },
        )
    except Exception:  # noqa: BLE001 — сеть тоже результат пробы
        return 0, time.perf_counter() - started, 0
    spent = time.perf_counter() - started
    tokens = 0
    if response.status_code < 400:
        tokens = (response.json().get("usage") or {}).get("total_tokens", 0)
    return response.status_code, spent, tokens


async def probe(model: str, levels: list[int], base_url: str, headers: dict) -> int:
    """Прогнать уровни параллельности. Возвращает максимальный безопасный."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    print(f"\n{model}")
    safe = 0
    total_tokens = 0

    async with httpx.AsyncClient(timeout=60) as client:
        for level in levels:
            results = await asyncio.gather(
                *(one_call(client, url, headers, model) for _ in range(level))
            )
            codes = [code for code, _, _ in results]
            times = [spent for _, spent, _ in results]
            total_tokens += sum(tokens for _, _, tokens in results)

            ok = sum(1 for c in codes if 200 <= c < 400)
            limited = sum(1 for c in codes if c == 429)
            other = [c for c in codes if c >= 400 and c != 429] + [c for c in codes if c == 0]

            mark = f"{GREEN} ok {OFF}" if ok == level else f"{RED}лимит{OFF}"
            note = f"прошло {ok}/{level}"
            if limited:
                note += f", 429: {limited}"
            if other:
                note += f", прочие коды: {sorted(set(other))}"
            print(f"  {mark} параллельно {level:>2}  {note}{DIM}"
                  f"  медленнейший {max(times):.1f} с{OFF}")

            if ok == level:
                safe = level
            else:
                break
            # Пауза между уровнями: лимит часто считается в окне, и без паузы
            # следующий уровень упрётся в хвост предыдущего.
            await asyncio.sleep(2)

    print(f"{DIM}  израсходовано токенов: {total_tokens}{OFF}")
    return safe


async def list_models(base_url: str, headers: dict) -> list[str]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
    if response.status_code >= 400:
        return []
    items = response.json().get("data") or []
    # Эмбеддинги не умеют chat/completions — пробовать их бессмысленно.
    return [m["id"] for m in items if "embed" not in m["id"].lower()]


async def main() -> int:
    parser = argparse.ArgumentParser(description="Предел параллельных запросов к шлюзу")
    parser.add_argument("--model", action="append", help="проверить конкретную модель")
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 2, 4, 6, 8])
    args = parser.parse_args()

    settings = get_settings()
    base_url = settings.llm_base_url
    key = settings.llm_api_key
    if not key:
        print("нет ключа: задайте OPENAI_API_KEY или LLM_API_KEY")
        return 2

    headers = {
        "Authorization": f"{settings.llm_auth_scheme} {key}",
        "Content-Type": "application/json",
    }
    print(f"шлюз: {base_url}")

    models = args.model or await list_models(base_url, headers)
    if not models:
        models = [settings.llm_model] if settings.llm_model else []
    if not models:
        print("не удалось получить список моделей, укажите --model")
        return 2

    verdicts: dict[str, int] = {}
    for model in models:
        verdicts[model] = await probe(model, sorted(args.levels), base_url, headers)

    print(f"\n{'=' * 60}\nБезопасная параллельность:")
    for model, safe in verdicts.items():
        value = str(safe) if safe else "даже 1 не прошёл"
        print(f"  {model:<48} {value}")

    best = max(verdicts.values(), default=0)
    if best:
        print(f"\nСтавьте LLM_MAX_PARALLEL={best} "
              f"(или меньше, если рядом работает кто-то из команды)")
    return 0 if best else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
