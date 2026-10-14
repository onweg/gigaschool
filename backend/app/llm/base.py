"""Контракт провайдера LLM.

Архитектурное требование кейса: на финале разрешены только модели из каталога
Cloud.ru Evolution Foundation Models. Поэтому вся работа с моделью идёт через
этот интерфейс, а конкретный провайдер выбирается переменными окружения.
Переезд Yandex → Cloud.ru = смена LLM_BASE_URL / LLM_MODEL без правок логики.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __iadd__(self, other: "Usage") -> "Usage":
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        return self


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        json_mode: bool = False,
    ) -> Completion: ...

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4000,
        retries: int = 2,
    ) -> tuple[dict, Usage]:
        """Ответ модели как JSON. Модели регулярно оборачивают JSON в текст,
        поэтому парсинг устойчивый, с ретраями и явной ошибкой наверх."""
        usage = Usage()
        last_error = ""
        for attempt in range(retries + 1):
            hint = (
                system
                if attempt == 0
                else system + f"\n\nПредыдущий ответ не распарсился: {last_error}. "
                "Верни ТОЛЬКО валидный JSON без пояснений и markdown."
            )
            result = await self.complete(
                system=hint,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
            )
            usage += result.usage
            try:
                return extract_json(result.text), usage
            except ValueError as exc:
                last_error = str(exc)
        raise LLMError(f"Модель не вернула валидный JSON: {last_error}")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Достать JSON-объект из ответа модели."""
    text = text.strip()
    if not text:
        raise ValueError("пустой ответ")
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("в ответе нет JSON-объекта") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"битый JSON: {exc.msg} (поз. {exc.pos})") from None
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект")
    return data
