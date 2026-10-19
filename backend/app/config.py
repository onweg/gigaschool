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


#: Удалённые настройки. Молча игнорировать их нельзя: на сервере в .env
#: останется старая строка, и человек будет уверен, что она что-то делает.
RETIRED_ENV = {
    "LLM_ALLOW_ANY_HOST": (
        "он снимал ограничение на адрес модели целиком. Уберите его из .env; "
        "если нужен другой шлюз — LLM_EXTRA_HOSTS=<домен>"
    ),
}


def extra_hosts(value: str) -> tuple[str, ...]:
    """Разобрать LLM_EXTRA_HOSTS и отказать на всём, что открывает слишком много.

    Хост сверяется по суффиксу, поэтому «com» разрешил бы любой .com-адрес,
    а «*» — вообще всё. Принимаем только полные доменные имена.
    """
    hosts: list[str] = []
    for raw in value.split(","):
        host = raw.strip().lower()
        if not host:
            continue
        if "*" in host or "/" in host or "." not in host.strip("."):
            raise ConfigError(
                f"LLM_EXTRA_HOSTS: {raw.strip()!r} — не доменное имя. Укажите домен "
                f"целиком, например llm.example-platform.ru; маски и зоны не принимаются"
            )
        hosts.append(host.strip("."))
    return tuple(hosts)


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
def _number(name: str, default: str, kind: type = int):  # noqa: ANN205
    """Число из окружения с понятной ошибкой вместо стека вызовов."""
    raw = os.getenv(name, default)
    try:
        return kind(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r}: ожидалось число") from None


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
    mock_latency_ms: int = Field(default_factory=lambda: _number("MOCK_LATENCY_MS", "0"))
    # Yandex ждёт «Api-Key», OpenAI-совместимые шлюзы (Cloud.ru) — «Bearer».
    llm_auth_scheme: str = Field(
        default_factory=lambda: _env(
            "LLM_AUTH_SCHEME",
            default="Bearer" if os.getenv("OPENAI_API_KEY") else "Api-Key",
        )
    )
    llm_timeout: float = Field(default_factory=lambda: _number("LLM_TIMEOUT", "180", float))
    #: Сколько запросов к модели держим одновременно. Шлюз хакатона даёт
    #: лимит на команду, а не на процесс, и отвечает 429 — там нужно 1–2.
    llm_max_parallel: int = Field(default_factory=lambda: _number("LLM_MAX_PARALLEL", "2"))
    llm_json_mode: bool = Field(default_factory=lambda: os.getenv("LLM_JSON_MODE", "1") == "1")
    #: Кэш ответов модели на диске: повторный прогон не тратит бюджет.
    llm_cache: bool = Field(default_factory=lambda: os.getenv("LLM_CACHE", "0") == "1")
    #: Сколько часов живёт запись кэша. В нём лежат тексты программ —
    #: хранить их вечно незачем.
    llm_cache_ttl_hours: int = Field(default_factory=lambda: _number("LLM_CACHE_TTL_HOURS", "24"))
    #: Предел числа записей: кэш не должен расти без конца.
    llm_cache_max_files: int = Field(
        default_factory=lambda: _number("LLM_CACHE_MAX_FILES", "2000")
    )
    #: Дополнительные разрешённые хосты, через запятую. Разрешают конкретный
    #: домен, а не снимают проверку целиком: выключателя «любой адрес» нет
    #: намеренно — опция, которая открывает запрещённые API, рано или поздно
    #: окажется включённой в бою.
    llm_extra_hosts: str = Field(default_factory=lambda: os.getenv("LLM_EXTRA_HOSTS", ""))
    # Стоимость токенов для расчёта экономики генерации (руб. за 1000 токенов)
    price_prompt_per_1k: float = Field(
        default_factory=lambda: _number("PRICE_PROMPT_PER_1K", "0.20", float)
    )
    price_completion_per_1k: float = Field(
        default_factory=lambda: _number("PRICE_COMPLETION_PER_1K", "0.60", float)
    )
    methodist_hour_cost: float = Field(
        default_factory=lambda: _number("METHODIST_HOUR_COST", "800", float)
    )
    manual_hours_per_program: float = Field(
        default_factory=lambda: _number("MANUAL_HOURS_PER_PROGRAM", "60", float)
    )

    #: Токен для чтения телеметрии. В ней лежат тексты запросов методистов
    #: и стоимость генераций — наружу это отдавать нельзя. Пусто — раздел
    #: статистики закрыт целиком.
    metrics_token: str = Field(default_factory=lambda: os.getenv("METRICS_TOKEN", ""))

    # --- Рабочие пороги: разумно менять под площадку и нагрузку -------------
    #: Тестовых вопросов на тему, когда методист не назвал общее число.
    questions_per_topic: int = Field(
        default_factory=lambda: _number("QUESTIONS_PER_TOPIC", "2")
    )
    #: Сколько первых тем получают слайды, если модули не выбраны явно.
    #: Эталонный чек-лист просит слайды «для первых двух занятий».
    slide_topics: int = Field(default_factory=lambda: _number("SLIDE_TOPICS", "2"))
    #: Предел уточняющих вопросов за раз. Чек-лист кейса: «не больше пяти».
    max_clarifying_questions: int = Field(
        default_factory=lambda: _number("MAX_CLARIFYING_QUESTIONS", "5")
    )
    #: Предел размера референса, МБ: методист прикладывает программу, не архив.
    max_reference_mb: int = Field(
        default_factory=lambda: _number("MAX_REFERENCE_MB", "25")
    )
    #: Сколько символов референса уходит в промпт после сжатия до структуры.
    reference_max_chars: int = Field(
        default_factory=lambda: _number("REFERENCE_MAX_CHARS", "6000")
    )
    #: Сколько реплик переписки держим в сессии.
    max_messages: int = Field(default_factory=lambda: _number("MAX_MESSAGES", "60"))

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
    _check_ranges(s)
    for name, advice in RETIRED_ENV.items():
        if os.getenv(name):
            raise ConfigError(f"{name} больше не поддерживается: {advice}")
    # Провайдер можно не задавать: есть ключ и модель — работаем с моделью.
    # Так запуск в чужой среде не скатывается на заглушку из-за забытой переменной.
    wants_live = s.llm_provider in ("", "openai_compat")
    if wants_live and s.llm_api_key and s.llm_model:
        if not s.llm_base_url:
            raise ConfigError(
                "задан ключ модели, но не задан её адрес: укажите OPENAI_BASE_URL "
                "(или LLM_BASE_URL) — например, шлюз площадки из /opt/hackathon/ai.env"
            )
        extra = extra_hosts(s.llm_extra_hosts)
        if not host_allowed(s.llm_base_url, extra):
            raise ConfigError(
                f"адрес модели {s.llm_base_url!r} не разрешён. Кейс допускает только "
                f"модели Cloud.ru: {', '.join(ALLOWED_LLM_HOSTS)}. Если площадка "
                f"выдала шлюз на другом домене, добавьте именно его: "
                f"LLM_EXTRA_HOSTS=<домен>."
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

            return CachedProvider(
                provider,
                Path(s.data_dir) / "llm-cache",
                ttl_hours=s.llm_cache_ttl_hours,
                max_files=s.llm_cache_max_files,
            )
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


#: Допустимые диапазоны числовых настроек. Ноль вопросов на тему или
#: отрицательный таймаут дают не ошибку, а тихо испорченный комплект.
_RANGES = {
    "llm_max_parallel": (1, 16),
    "llm_timeout": (5, 3600),
    "questions_per_topic": (1, 20),
    "slide_topics": (1, 20),
    "max_clarifying_questions": (1, 10),
    "max_reference_mb": (1, 200),
    "reference_max_chars": (500, 100_000),
    "max_messages": (10, 1000),
    "llm_cache_ttl_hours": (1, 24 * 30),
    "llm_cache_max_files": (10, 100_000),
}


def _check_ranges(s: Settings) -> None:
    for field, (low, high) in _RANGES.items():
        value = getattr(s, field)
        if not low <= value <= high:
            env = field.upper()
            raise ConfigError(f"{env}={value}: допустимо от {low} до {high}")


def describe_config() -> list[str]:
    """Строки для журнала запуска: с чем именно поднялся сервис.

    Нужны, чтобы режим работы был виден сразу, а не выяснялся по текстам
    программ. Ключ печатается обрезанным: журналы читают и пересылают.
    """
    s = get_settings()
    key = s.llm_api_key
    masked = f"{key[:4]}…{key[-3:]}" if len(key) > 10 else ("задан" if key else "нет")
    lines = [
        f"провайдер:      {s.llm_provider or 'выбран по ключу'}",
        f"адрес модели:   {s.llm_base_url or '—'}",
        f"модель:         {s.llm_model or '—'}",
        f"ключ:           {masked}",
        f"одновременно:   {s.llm_max_parallel} запрос(ов)",
        f"кэш ответов:    {'включён' if s.llm_cache else 'выключен'}",
        f"данные:         {Path(s.data_dir).resolve()}",
    ]
    if s.llm_extra_hosts:
        lines.append(f"доп. хосты:     {', '.join(extra_hosts(s.llm_extra_hosts))}")
    return lines


def check_config() -> list[str]:
    """Предупреждения, которые не мешают работе, но о них надо знать."""
    s = get_settings()
    warnings: list[str] = []
    data = Path(s.data_dir)
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        warnings.append(f"каталог данных {data} недоступен для записи: {exc}")
    if s.llm_extra_hosts:
        warnings.append(
            f"разрешены хосты вне Cloud.ru: {', '.join(extra_hosts(s.llm_extra_hosts))}. "
            "Для сдачи кейса уберите LLM_EXTRA_HOSTS"
        )
    if not s.metrics_token:
        warnings.append(
            "METRICS_TOKEN не задан — раздел статистики закрыт. "
            "Задайте токен, чтобы открыть его себе"
        )
    if s.max_clarifying_questions > 5:
        warnings.append(
            f"MAX_CLARIFYING_QUESTIONS={s.max_clarifying_questions}: чек-лист кейса "
            f"требует не больше пяти уточняющих вопросов"
        )
    if s.llm_max_parallel > 4:
        warnings.append(
            f"LLM_MAX_PARALLEL={s.llm_max_parallel}: шлюз площадки отвечает 429 "
            f"уже на трёх одновременных запросах"
        )
    return warnings
