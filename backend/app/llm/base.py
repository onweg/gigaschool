"""Контракт провайдера LLM.

Архитектурное требование кейса: на финале разрешены только модели из каталога
Cloud.ru Evolution Foundation Models. Поэтому вся работа с моделью идёт через
этот интерфейс, а конкретный провайдер выбирается переменными окружения.
В профиле хакатона разрешены только GigaChat 3 Pro и GigaChat 3.5 Reasoning.
Тестовый профиль VPS использует одну настроенную сервером модель YandexGPT.
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
    cost_rub: float | None = None

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __iadd__(self, other: "Usage") -> "Usage":
        if self.cost_rub is not None or other.cost_rub is not None:
            from app.llm.models import token_cost
            self.cost_rub = (self.cost_rub if self.cost_rub is not None else token_cost('', self.prompt_tokens, self.completion_tokens)) + (other.cost_rub if other.cost_rub is not None else token_cost('', other.prompt_tokens, other.completion_tokens))
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        return self


@dataclass
class Completion:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""


#: Как выглядят ключи и заголовки авторизации в тексте ошибок. Шлюзы любят
#: возвращать в ответе то, что им прислали, и ключ доехал бы до интерфейса,
#: журнала и телеметрии.
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer|api-key)\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\b(?:sk|hub|AQVN)[-_A-Za-z0-9]{10,}"),
)


def redact(text: str, *secrets: str) -> str:
    """Убрать ключи из текста, который увидит человек или запишет журнал."""
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(0).split()[0] + " ***" if " " in m.group(0) else "***", text)
    return text


class LLMError(RuntimeError):
    """Сбой обращения к модели. Текст очищен от ключей при создании —
    поэтому его можно показывать в интерфейсе и писать в журнал."""

    def __init__(self, message: str = "", *secrets: str) -> None:
        super().__init__(redact(str(message), *secrets))


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

#: Как модели пишут «нет значения» строкой вместо JSON-null. Живой случай
#: на GigaChat: "total_hours": "null" — и float("null") ронял весь разбор.
_NULL_WORDS = {"null", "none", "nil", "undefined", "n/a", "не указан", "не указано", "не указана", "неизвестно"}


def _drop_null_words(value: object) -> object:
    if isinstance(value, str) and value.strip().lower() in _NULL_WORDS:
        return None
    if isinstance(value, dict):
        return {k: _drop_null_words(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_drop_null_words(v) for v in value]
    return value


_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def as_number(value: object, default: float | None = None) -> float | None:
    """Число из ответа модели: 200, "200", "200 часов", "0,8". Иначе default.

    Модель регулярно отдаёт числа строками и с единицами измерения; падать
    из-за этого на разборе нельзя — значение просто считается неизвестным,
    и агент спросит его у методиста.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _NUMBER.search(value)
        if match:
            return float(match.group(0).replace(",", "."))
    return default


def extract_json(text: str) -> dict:
    """Достать JSON-объект из ответа модели."""
    text = text.strip()
    if not text:
        raise ValueError("пустой ответ")
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("в ответе нет JSON-объекта") from None
        chunk = text[start : end + 1]
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError as first:
            # Модели вставляют в строки сырые переводы строк и табуляции —
            # по стандарту их положено экранировать, и парсер падает с
            # «Invalid control character». Смысл ответа при этом цел, поэтому
            # повторяем в нестрогом режиме, где такие символы разрешены.
            try:
                data = json.loads(chunk, strict=False)
            except json.JSONDecodeError:
                raise ValueError(f"битый JSON: {first.msg} (поз. {first.pos})") from None
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект")
    return _drop_null_words(data)  # type: ignore[return-value]
