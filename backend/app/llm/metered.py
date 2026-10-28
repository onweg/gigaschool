"""Обёртка провайдера, которая считает каждый вызов модели.

Декоратор вокруг LLMProvider: логика агента о телеметрии не знает, а мы
получаем латентность, токены и отказы в разрезе навыков. Навык достаём из
маркера `SKILL:<name>` в системном промпте — он там уже есть для диспетчера
mock-провайдера. Номер сессии приходит через contextvar: пайплайн вызывает
провайдера глубоко внутри и прокидывать его параметром пришлось бы через
десяток функций.
"""
from __future__ import annotations

import re
import time
from contextvars import ContextVar

from app.llm.base import Completion, LLMProvider
from app.metrics import record

_SKILL = re.compile(r"SKILL:([a-z_]+)")
current_session: ContextVar[str] = ContextVar("current_session", default="")


class MeteredProvider(LLMProvider):
    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner

    @property
    def name(self):
        return self._inner.name

    @property
    def model(self):
        return self._inner.model

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        json_mode: bool = False,
    ) -> Completion:
        match = _SKILL.search(system)
        skill = match.group(1) if match else "unknown"
        started = time.perf_counter()
        model = self.model
        try:
            result = await self._inner.complete(
                system=system,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
        except Exception as exc:  # noqa: BLE001 — записываем отказ и пробрасываем
            record.llm_call(
                session_id=current_session.get(),
                skill=skill,
                provider=self.name,
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        from app.llm.models import token_cost
        result.usage.cost_rub = token_cost(model, result.usage.prompt_tokens, result.usage.completion_tokens)
        record.llm_call(
            session_id=current_session.get(),
            skill=skill,
            provider=self.name,
            model=result.model or self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            ok=True,
            cost_rub=result.usage.cost_rub,
        )
        return result
