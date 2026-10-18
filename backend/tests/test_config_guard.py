"""Ограничение кейса: модели только из каталога Cloud.ru.

Проверяющие отметили это прямо: «адаптер OpenAI-совместимости технически
допускает перенаправление запросов на внешние эндпоинты, то есть ограничение
кейса на использование только Cloud.ru выполнено не полностью».

Адрес шлюза — не настройка, а граница дозволенного, поэтому она проверяется
кодом, а не договорённостью.
"""
from __future__ import annotations

import pytest

from app.config import ConfigError, build_provider, get_settings, host_allowed


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch: pytest.MonkeyPatch):
    """Каждому тесту — своё окружение и свежие настройки."""
    for name in (
        "LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
        "LLM_ALLOW_ANY_HOST", "LLM_EXTRA_HOSTS",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "url",
    [
        "https://foundation-models.api.cloud.ru/v1",
        "https://hub.gigaschool.ru/api/v1/llm/v1",
        "http://localhost:8080/v1",
    ],
)
def test_allowed_hosts(url: str) -> None:
    assert host_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://llm.api.cloud.yandex.net/v1",
        "https://generativelanguage.googleapis.com/v1",
        "https://cloud.ru.evil.example.com/v1",  # подделка под разрешённый домен
        "",
    ],
)
def test_forbidden_hosts(url: str) -> None:
    assert not host_allowed(url)


def test_foreign_endpoint_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Запуск на чужом эндпоинте невозможен — не «не рекомендуется», а нельзя."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4")
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="не разрешён"):
        build_provider()


def test_cloud_ru_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat/GigaChat-3-Pro")
    get_settings.cache_clear()

    assert build_provider().name == "openai_compat"


def test_explicit_override_is_possible_for_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """Разработке нужен Yandex — но только через явный флаг, не по умолчанию."""
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.api.cloud.yandex.net/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt://folder/yandexgpt/latest")
    monkeypatch.setenv("LLM_ALLOW_ANY_HOST", "1")
    get_settings.cache_clear()

    assert build_provider().name == "openai_compat"


def test_extra_host_can_be_added(monkeypatch: pytest.MonkeyPatch) -> None:
    """Площадка может выдать шлюз на другом домене — это настраивается."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.example-platform.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat")
    monkeypatch.setenv("LLM_EXTRA_HOSTS", "example-platform.ru")
    get_settings.cache_clear()

    assert build_provider().name == "openai_compat"


def test_key_without_endpoint_says_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Молчаливый откат на заглушку — худший сценарий: демо выглядит рабочим."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat")
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="не задан её адрес"):
        build_provider()


def test_offline_demo_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без всяких ключей — офлайн-заглушка, как и было."""
    get_settings.cache_clear()
    assert build_provider().name == "mock"


def test_mock_is_refused_when_a_real_key_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Заглушка при живом ключе — почти всегда ошибка запуска.

    Сервис выглядел бы работающим, но выдавал болванки вместо программ.
    Проверяющие отметили это как «риск подстройки под контрольные примеры».
    """
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat")
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="LLM_PROVIDER=mock"):
        build_provider()


def test_mock_does_not_guess_the_request() -> None:
    """Заглушка не разбирает запрос: иначе это шпаргалка под контрольные примеры.

    Тип программы, объём и форму обучения извлекает pipeline/intake.py —
    одинаково и на заглушке, и на живой модели.
    """
    import asyncio
    import json

    from app.llm.mock import MockProvider

    school = "общеразвивающая программа для школьников на 16 часов"
    pk = "программа повышения квалификации на 72 часа для сотрудников банков"

    async def intake_answer(text: str) -> dict:
        result = await MockProvider().complete(system="SKILL:intake", user=text)
        return json.loads(result.text)

    first = asyncio.run(intake_answer(school))
    second = asyncio.run(intake_answer(pk))

    assert first == second, "ответ заглушки не должен зависеть от текста запроса"
    assert "program_type" not in first
    assert "total_hours" not in first


def test_startup_report_hides_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Журнал запуска читают и пересылают — ключ печатается обрезанным."""
    from app.config import describe_config

    monkeypatch.setenv("OPENAI_API_KEY", "AQVNsecret0123456789")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat")
    get_settings.cache_clear()

    report = "\n".join(describe_config())

    assert "AQVNsecret0123456789" not in report
    assert "AQVN" in report, "по началу ключа видно, тот ли он"
    assert "foundation-models.api.cloud.ru" in report
    assert "GigaChat" in report


def test_warning_when_host_check_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Снятое ограничение не должно потеряться: с ним нельзя идти в сдачу."""
    from app.config import check_config

    monkeypatch.setenv("LLM_ALLOW_ANY_HOST", "1")
    get_settings.cache_clear()

    assert any("LLM_ALLOW_ANY_HOST" in w for w in check_config())


def test_no_warnings_in_a_clean_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import check_config

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat")
    get_settings.cache_clear()

    assert check_config() == []
