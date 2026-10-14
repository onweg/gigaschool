"""Провайдер для любого OpenAI-совместимого эндпоинта.

Проверено с:
  * Yandex Foundation Models — https://llm.api.cloud.yandex.net/v1,
    модель `gpt://<folder-id>/yandexgpt/latest`, авторизация `Api-Key <key>`
  * Cloud.ru Evolution Foundation Models — OpenAI-совместимый эндпоинт,
    авторизация `Bearer <token>` (для финальной сдачи кейса)
"""
from __future__ import annotations

import httpx

from app.llm.base import Completion, LLMError, LLMProvider, Usage


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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.auth_scheme = auth_scheme
        self.timeout = timeout
        self.name = name
        self.supports_json_mode = supports_json_mode
        self.extra_headers = extra_headers or {}

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        json_mode: bool = False,
    ) -> Completion:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"{self.auth_scheme} {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            if response.status_code >= 400:
                raise LLMError(
                    f"{self.name}: HTTP {response.status_code} — {response.text[:500]}"
                )
            data = response.json()

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"{self.name}: неожиданный формат ответа: {data}") from exc
        raw_usage = data.get("usage") or {}
        return Completion(
            text=text,
            usage=Usage(
                prompt_tokens=raw_usage.get("prompt_tokens", 0),
                completion_tokens=raw_usage.get("completion_tokens", 0),
            ),
            model=data.get("model", self.model),
        )
