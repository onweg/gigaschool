"""Кэш ответов модели на диске.

На хакатоне бюджет площадки — сотня рублей на команду, а прогон по пяти
запросам стоит десятки. При этом половина прогонов повторяет одно и то же:
меняешь одну строчку в коде и гоняешь заново весь набор. Платить за это
второй раз незачем.

Ключ кэша — модель плюс полный текст запроса, так что любое изменение
промпта или навыка даёт новый ключ и честное обращение к модели.

    LLM_CACHE=1 python3 scripts/run_open_requests.py   # первый прогон платный
    LLM_CACHE=1 python3 scripts/run_open_requests.py   # второй бесплатный

Побочная польза: записанный прогон превращается в офлайн-демо, которое
повторяет настоящие тексты модели без сети. Если на защите упадёт интернет
или кончится лимит, показывать будет что.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.llm.base import Completion, LLMProvider, Usage


class CachedProvider(LLMProvider):
    """Обёртка: сначала смотрит на диск, при промахе идёт к модели."""

    def __init__(self, inner: LLMProvider, directory: str | Path) -> None:
        self.inner = inner
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"{self.inner.name}+cache"

    @property
    def model(self) -> str:  # type: ignore[override]
        return self.inner.model

    def _path(self, system: str, user: str, json_mode: bool) -> Path:
        key = hashlib.sha256(
            "\x00".join([self.inner.model, system, user, str(json_mode)]).encode()
        ).hexdigest()
        return self.dir / f"{key}.json"

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        json_mode: bool = False,
    ) -> Completion:
        path = self._path(system, user, json_mode)
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved = None  # битый файл просто перезапишем
            if saved:
                self.hits += 1
                # Токены записи не начисляем: за этот ответ уже заплачено.
                return Completion(text=saved["text"], usage=Usage(), model=saved.get("model", ""))

        self.misses += 1
        result = await self.inner.complete(
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        try:
            path.write_text(
                json.dumps(
                    {"text": result.text, "model": result.model},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # не можем записать — не повод ронять генерацию
        return result
