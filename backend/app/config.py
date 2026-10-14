"""Конфигурация. Провайдер LLM выбирается окружением, код об этом не знает."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    # LLM: mock | openai_compat
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://llm.api.cloud.yandex.net/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_auth_scheme: str = os.getenv("LLM_AUTH_SCHEME", "Api-Key")
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "180"))
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
    if s.llm_provider == "openai_compat" and s.llm_api_key and s.llm_model:
        return OpenAICompatProvider(
            base_url=s.llm_base_url,
            api_key=s.llm_api_key,
            model=s.llm_model,
            auth_scheme=s.llm_auth_scheme,
            timeout=s.llm_timeout,
            name=s.llm_provider,
            supports_json_mode=s.llm_json_mode,
        )
    return MockProvider()
