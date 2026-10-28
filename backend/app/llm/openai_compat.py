"""Провайдер для любого OpenAI-совместимого эндпоинта.

Проверено с:
  * Cloud.ru Evolution Foundation Models — OpenAI-совместимый эндпоинт,
    авторизация `Bearer <token>` (для финальной сдачи кейса)
"""
from __future__ import annotations

import asyncio
import logging
import random
from urllib.parse import urlparse

import httpx

from app.llm.base import Completion, LLMError, LLMProvider, Usage

log = logging.getLogger("agent.llm")

#: Коды, после которых есть смысл повторить: перегруз шлюза и лимит запросов.
RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})

#: Секунды на установку соединения, отправку запроса и ожидание свободного
#: соединения. Чтение ответа ограничено LLM_TIMEOUT: модель пишет долго законно.
CONNECT_TIMEOUT = 10.0
WRITE_TIMEOUT = 30.0
POOL_TIMEOUT = 30.0

#: Что значит код ответа для человека — чтобы в интерфейсе было понятно,
#: чинить ключ, адрес или ждать.
_REASONS = {
    400: "шлюз отклонил запрос",
    401: "ключ модели не подошёл — проверьте OPENAI_API_KEY",
    403: "у ключа нет доступа к этой модели",
    404: "модель или адрес не найдены — проверьте OPENAI_MODEL и OPENAI_BASE_URL",
    413: "запрос слишком большой для модели",
    422: "шлюз не принял параметры запроса",
}


def _explain(status: int) -> str:
    return _REASONS.get(status, f"шлюз ответил ошибкой {status}")


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        auth_scheme: str = "Bearer",
        timeout: float = 180.0,
        name: str = "openai-compat",
        supports_json_mode: bool = True,
        extra_headers: dict[str, str] | None = None,
        max_parallel: int = 4,
        max_retries: int = 5,
        allowed_hosts: tuple[str, ...] | None = None,
    ) -> None:
        # Вторая линия защиты: конфигурация уже проверила адрес, но провайдер
        # можно создать и в обход неё (скрипт, тест, будущий код). Ключ не
        # должен уйти на хост вне списка ни при каком пути создания.
        host = (urlparse(base_url).hostname or "").lower()
        if allowed_hosts is not None and not any(
            host == h or host.endswith("." + h) for h in allowed_hosts
        ):
            log.warning("адрес модели отклонён: хост %s вне списка разрешённых", host or "—")
            raise LLMError(f"{name}: хост {host or '—'} не входит в список разрешённых")
        self.host = host
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.auth_scheme = auth_scheme
        self.timeout = timeout
        self.name = name
        self.supports_json_mode = supports_json_mode
        self.extra_headers = extra_headers or {}
        self.max_retries = max_retries
        # Шлюз площадки отвечает 429 «too many concurrent requests from this
        # team»: лимит общий на команду, а не на процесс. Поэтому ограничиваем
        # одновременные запросы здесь — выше по стеку об этом знать не нужно.
        self._gate = asyncio.Semaphore(max_parallel)

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        json_mode: bool = False,
    ) -> Completion:
        reasoning = self.model == "ai-sage/GigaChat3.5-432B-A28B-Reasoning"
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens + 8192 if reasoning else max_tokens,
        }
        if json_mode and self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"{self.auth_scheme} {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        data = await self._post(payload, headers)

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"{self.name}: неожиданный формат ответа: {data}") from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMError(f"{self.name}: модель {self.model} не вернула итоговый текст. "
                           "Возможно, лимит ответа потрачен на рассуждения. Повторите запрос или выберите GigaChat 3 Pro.")
        raw_usage = data.get("usage") or {}
        return Completion(
            text=text,
            usage=Usage(
                prompt_tokens=raw_usage.get("prompt_tokens", 0),
                completion_tokens=raw_usage.get("completion_tokens", 0),
            ),
            model=data.get("model", self.model),
        )

    async def _post(self, payload: dict, headers: dict) -> dict:
        """POST с повтором при перегрузе шлюза.

        Лимит у площадки общий на команду, поэтому отказ — обычное дело, а не
        поломка: ждём и пробуем снова. Пауза растёт вдвое с каждой попыткой,
        со случайной добавкой, иначе параллельные задачи синхронно долбятся
        в одну и ту же секунду и снова получают 429.
        """
        last = ""
        # Таймауты раздельно: до недоступного шлюза достучаться не выйдет и за
        # три минуты — это видно за секунды; а ответ на длинный промпт модель
        # законно пишет долго, его ждём полный срок.
        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT, read=max(self.timeout, 300) if self.model.endswith('-Reasoning') else self.timeout,
            write=WRITE_TIMEOUT, pool=POOL_TIMEOUT
        )
        for attempt in range(self.max_retries):
            after = None
            async with self._gate:
                try:
                    # Перенаправления не выполняем: ответ 3xx увёл бы запрос
                    # вместе с ключом на хост, который проверку не проходил.
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                        response = await client.post(
                            f"{self.base_url}/chat/completions", json=payload, headers=headers
                        )
                except httpx.TimeoutException as exc:
                    # Шлюз не ответил вовремя — та же ситуация, что 504: повторяем.
                    last = f"нет ответа за отведённое время ({type(exc).__name__})"
                    response = None
                except httpx.TransportError as exc:
                    # Соединение не установилось или оборвалось. Раньше это
                    # улетало сырым исключением httpx без повтора и без объяснения.
                    last = f"сеть недоступна: {exc.__class__.__name__}"
                    response = None
                if response is not None:
                    if 300 <= response.status_code < 400:
                        target = urlparse(response.headers.get("location", "")).hostname or "—"
                        log.warning("шлюз %s перенаправляет на %s — переход не выполнен", self.host, target)
                        raise LLMError(
                            f"{self.name}: шлюз перенаправляет запрос на {target} "
                            f"(HTTP {response.status_code}). Переход не выполнен, чтобы ключ "
                            f"не ушёл на непроверенный адрес: укажите в OPENAI_BASE_URL "
                            f"конечный адрес",
                            self.api_key,
                        )
                    if response.status_code < 400:
                        try:
                            return response.json()
                        except ValueError:
                            raise LLMError(
                                f"{self.name}: шлюз вернул не JSON (HTTP {response.status_code})",
                                self.api_key,
                            ) from None
                    last = f"HTTP {response.status_code} — {response.text[:300]}"
                    if response.status_code not in RETRYABLE:
                        raise LLMError(f"{self.name}: {_explain(response.status_code)}. {last}", self.api_key)
                    # Сервер может сам сказать, сколько ждать.
                    after = response.headers.get("retry-after")
            delay = float(after) if after and after.isdigit() else 2**attempt
            await asyncio.sleep(min(delay, 30) + random.uniform(0, 0.5))
        raise LLMError(f"{self.name}: {last} (после {self.max_retries} попыток)", self.api_key)
