"""Конфигурация. Провайдер LLM выбирается окружением, код об этом не знает."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


#: Среда хакатона кладёт доступы к моделям Cloud.ru в OPENAI_*-переменные
#: (/opt/hackathon/ai.env). Принимаем оба написания: свои LLM_* в приоритете,
#: иначе берём то, что дала площадка, — иначе запуск молча уходит на заглушку.
def _env(*names: str, default: str = "") -> str:
    for name in names:
        if value := os.getenv(name):
            return value
    return default


class Settings(BaseModel):
    # LLM: mock | openai_compat
    llm_provider: str = os.getenv("LLM_PROVIDER", "")
    llm_base_url: str = _env(
        "LLM_BASE_URL", "OPENAI_BASE_URL", default="https://llm.api.cloud.yandex.net/v1"
    )
    llm_api_key: str = _env("LLM_API_KEY", "OPENAI_API_KEY")
    llm_model: str = _env("LLM_MODEL", "OPENAI_MODEL")
    # Задержка mock-ответа, мс: офлайн-демо иначе отрабатывает мгновенно
    # и прогресс генерации не успевает показаться.
    mock_latency_ms: int = int(os.getenv("MOCK_LATENCY_MS", "0"))
    # Yandex ждёт «Api-Key», OpenAI-совместимые шлюзы (Cloud.ru) — «Bearer».
    llm_auth_scheme: str = _env(
        "LLM_AUTH_SCHEME", default="Bearer" if os.getenv("OPENAI_API_KEY") else "Api-Key"
    )
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "180"))
    #: Сколько запросов к модели держим одновременно. Шлюз хакатона даёт
    #: лимит на команду, а не на процесс, и отвечает 429 — там нужно 1–2.
    llm_max_parallel: int = int(_env("LLM_MAX_PARALLEL", default="2"))
    llm_json_mode: bool = os.getenv("LLM_JSON_MODE", "1") == "1"
    # Стоимость токенов для расчёта экономики генерации (руб. за 1000 токенов)
    price_prompt_per_1k: float = float(os.getenv("PRICE_PROMPT_PER_1K", "0.20"))
    price_completion_per_1k: float = float(os.getenv("PRICE_COMPLETION_PER_1K", "0.60"))
    methodist_hour_cost: float = float(os.getenv("METHODIST_HOUR_COST", "800"))
    manual_hours_per_program: float = float(os.getenv("MANUAL_HOURS_PER_PROGRAM", "60"))

    data_dir: str = os.getenv("DATA_DIR", "./data")
    cors_origins: list[str] = (
        os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def build_provider():  # noqa: ANN201 — возвращает LLMProvider
    from app.llm.mock import MockProvider
    from app.llm.openai_compat import OpenAICompatProvider

    s = get_settings()
    # Провайдер можно не задавать: есть ключ и модель — работаем с моделью.
    # Так запуск в чужой среде не скатывается на заглушку из-за забытой переменной.
    wants_live = s.llm_provider in ("", "openai_compat")
    if wants_live and s.llm_api_key and s.llm_model:
        return OpenAICompatProvider(
            base_url=s.llm_base_url,
            api_key=s.llm_api_key,
            model=s.llm_model,
            auth_scheme=s.llm_auth_scheme,
            timeout=s.llm_timeout,
            name=s.llm_provider or "openai_compat",
            supports_json_mode=s.llm_json_mode,
            max_parallel=s.llm_max_parallel,
        )
    return MockProvider()
