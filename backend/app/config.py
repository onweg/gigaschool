"""Конфигурация. Провайдер LLM выбирается окружением, код об этом не знает."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class ConfigError(RuntimeError):
    """Конфигурация не позволяет запуститься. Текст виден человеку."""


#: Кейс разрешает только модели из каталога Cloud.ru, поэтому адрес шлюза —
#: не просто настройка, а граница дозволенного. Проверяем его по списку, иначе
#: одной переменной окружения решение уводится на чужой эндпоинт.
ALLOWED_LLM_HOSTS = (
    "cloud.ru",
    "hub.gigaschool.ru",
    "localhost",
    "127.0.0.1",
)


def host_allowed(url: str, extra: tuple[str, ...] = ()) -> bool:
    """Хост из списка или его поддомен."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(
        host == allowed or host.endswith("." + allowed)
        for allowed in (*ALLOWED_LLM_HOSTS, *extra)
    )


#: Среда хакатона кладёт доступы к моделям Cloud.ru в OPENAI_*-переменные
#: (/opt/hackathon/ai.env). Принимаем оба написания: свои LLM_* в приоритете,
#: иначе берём то, что дала площадка, — иначе запуск молча уходит на заглушку.
def _env(*names: str, default: str = "") -> str:
    for name in names:
        if value := os.getenv(name):
            return value
    return default


class Settings(BaseModel):
    """Снимок окружения.

    Все значения читаются в момент создания объекта, а не при импорте модуля:
    иначе настройки нельзя ни переопределить в тестах, ни перечитать после
    смены окружения — и ошибка конфигурации всплывает уже в бою.
    """

    # LLM: пусто — выбирается сам по наличию ключа; mock — офлайн-заглушка
    llm_provider: str = Field(default_factory=lambda: os.getenv("LLM_PROVIDER", ""))
    #: Без умолчания: адрес шлюза задаёт площадка, а молчаливая подстановка
    #: чужого провайдера — ровно то, за что кейс снимает баллы.
    llm_base_url: str = Field(default_factory=lambda: _env("LLM_BASE_URL", "OPENAI_BASE_URL"))
    llm_api_key: str = Field(default_factory=lambda: _env("LLM_API_KEY", "OPENAI_API_KEY"))
    llm_model: str = Field(default_factory=lambda: _env("LLM_MODEL", "OPENAI_MODEL"))
    # Задержка mock-ответа, мс: офлайн-демо иначе отрабатывает мгновенно
    # и прогресс генерации не успевает показаться.
    mock_latency_ms: int = Field(default_factory=lambda: int(os.getenv("MOCK_LATENCY_MS", "0")))
    # Yandex ждёт «Api-Key», OpenAI-совместимые шлюзы (Cloud.ru) — «Bearer».
    llm_auth_scheme: str = Field(
        default_factory=lambda: _env(
            "LLM_AUTH_SCHEME",
            default="Bearer" if os.getenv("OPENAI_API_KEY") else "Api-Key",
        )
    )
    llm_timeout: float = Field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "180")))
    #: Сколько запросов к модели держим одновременно. Шлюз хакатона даёт
    #: лимит на команду, а не на процесс, и отвечает 429 — там нужно 1–2.
    llm_max_parallel: int = Field(default_factory=lambda: int(_env("LLM_MAX_PARALLEL", default="2")))
    llm_json_mode: bool = Field(default_factory=lambda: os.getenv("LLM_JSON_MODE", "1") == "1")
    #: Кэш ответов модели на диске: повторный прогон не тратит бюджет.
    llm_cache: bool = Field(default_factory=lambda: os.getenv("LLM_CACHE", "0") == "1")
    #: Снимает ограничение на адрес модели. Нужен на локальной разработке,
    #: где используется Yandex Foundation Models; в сдаче всегда выключен.
    llm_allow_any_host: bool = Field(
        default_factory=lambda: os.getenv("LLM_ALLOW_ANY_HOST", "0") == "1"
    )
    #: Дополнительные разрешённые хосты, через запятую, — на случай, если
    #: площадка выдаст шлюз на другом домене.
    llm_extra_hosts: str = Field(default_factory=lambda: os.getenv("LLM_EXTRA_HOSTS", ""))
    # Стоимость токенов для расчёта экономики генерации (руб. за 1000 токенов)
    price_prompt_per_1k: float = Field(
        default_factory=lambda: float(os.getenv("PRICE_PROMPT_PER_1K", "0.20"))
    )
    price_completion_per_1k: float = Field(
        default_factory=lambda: float(os.getenv("PRICE_COMPLETION_PER_1K", "0.60"))
    )
    methodist_hour_cost: float = Field(
        default_factory=lambda: float(os.getenv("METHODIST_HOUR_COST", "800"))
    )
    manual_hours_per_program: float = Field(
        default_factory=lambda: float(os.getenv("MANUAL_HOURS_PER_PROGRAM", "60"))
    )

    data_dir: str = Field(default_factory=lambda: os.getenv("DATA_DIR", "./data"))
    cors_origins: list[str] = Field(
        default_factory=lambda: os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
        ).split(",")
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
        if not s.llm_base_url:
            raise ConfigError(
                "задан ключ модели, но не задан её адрес: укажите OPENAI_BASE_URL "
                "(или LLM_BASE_URL) — например, шлюз площадки из /opt/hackathon/ai.env"
            )
        extra = tuple(h.strip() for h in s.llm_extra_hosts.split(",") if h.strip())
        if not s.llm_allow_any_host and not host_allowed(s.llm_base_url, extra):
            raise ConfigError(
                f"адрес модели {s.llm_base_url!r} не разрешён. Кейс допускает только "
                f"модели Cloud.ru: {', '.join(ALLOWED_LLM_HOSTS)}. "
                f"Для разработки на другом провайдере — LLM_ALLOW_ANY_HOST=1, "
                f"для другого шлюза площадки — LLM_EXTRA_HOSTS=<домен>."
            )
        provider = OpenAICompatProvider(
            base_url=s.llm_base_url,
            api_key=s.llm_api_key,
            model=s.llm_model,
            auth_scheme=s.llm_auth_scheme,
            timeout=s.llm_timeout,
            name=s.llm_provider or "openai_compat",
            supports_json_mode=s.llm_json_mode,
            max_parallel=s.llm_max_parallel,
        )
        if s.llm_cache:
            from app.llm.cache import CachedProvider

            return CachedProvider(provider, Path(s.data_dir) / "llm-cache")
        return provider

    # Заглушка — только для тестов и офлайн-демо. Если ключ задан, а провайдер
    # принудительно переведён на mock, это почти наверняка ошибка запуска:
    # сервис выглядит работающим, но выдаёт болванки вместо программ.
    if s.llm_provider == "mock" and s.llm_api_key:
        raise ConfigError(
            "LLM_PROVIDER=mock при заданном ключе модели. Заглушка не генерирует "
            "содержание — уберите LLM_PROVIDER или ключ, смотря что вы хотели."
        )
    return MockProvider()
