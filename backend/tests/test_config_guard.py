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


def test_development_host_is_allowed_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Разработке нужен Yandex — разрешаем его домен явно, а не всё подряд."""
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.api.cloud.yandex.net/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt://folder/yandexgpt/latest")
    monkeypatch.setenv("LLM_EXTRA_HOSTS", "llm.api.cloud.yandex.net")
    get_settings.cache_clear()

    assert build_provider().name == "openai_compat"


def test_extra_host_does_not_open_other_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Разрешённый Yandex не должен пропускать OpenAI: разрешение точечное."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4")
    monkeypatch.setenv("LLM_EXTRA_HOSTS", "llm.api.cloud.yandex.net")
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="не разрешён"):
        build_provider()


@pytest.mark.parametrize("value", ["*", "com", ".ru", "*.openai.com", "https://api.openai.com"])
def test_extra_hosts_refuse_wildcards_and_zones(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Хост сверяется по суффиксу, поэтому «com» открыл бы любой .com.

    Именно такую лазейку проверяющие назвали «опасной fallback-опцией».
    """
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat")
    monkeypatch.setenv("LLM_EXTRA_HOSTS", value)
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="не доменное имя"):
        build_provider()


def test_retired_blanket_flag_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Старый выключатель проверки не должен тихо ничего не делать.

    На сервере в .env останется старая строка — человек должен узнать, что
    она больше не действует, а не гадать, почему не работает.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat")
    monkeypatch.setenv("LLM_ALLOW_ANY_HOST", "1")
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="больше не поддерживается"):
        build_provider()


def test_there_is_no_switch_to_disable_the_host_check() -> None:
    """Опции «разрешить любой адрес» в настройках нет и быть не должно."""
    from app.config import Settings

    names = " ".join(Settings.model_fields).lower()
    assert "any_host" not in names
    assert "allow_any" not in names


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


def test_warning_when_extra_hosts_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Хост вне Cloud.ru не должен потеряться: с ним нельзя идти в сдачу."""
    from app.config import check_config

    monkeypatch.setenv("LLM_EXTRA_HOSTS", "llm.api.cloud.yandex.net")
    get_settings.cache_clear()

    assert any("вне Cloud.ru" in w for w in check_config())


def test_no_warnings_in_a_clean_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import check_config

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat")
    monkeypatch.setenv("METRICS_TOKEN", "s3cret")
    get_settings.cache_clear()

    assert check_config() == []


def test_metrics_are_closed_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """В телеметрии тексты запросов и стоимость — открытой по ссылке быть не должно."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    get_settings.cache_clear()
    from app.main import app

    client = TestClient(app)
    assert client.get("/api/metrics/overview").status_code == 503
    assert client.get("/api/metrics/export.csv?table=sessions").status_code == 503


def test_metrics_need_the_right_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("METRICS_TOKEN", "s3cret")
    get_settings.cache_clear()
    from app.main import app

    client = TestClient(app)
    assert client.get("/api/metrics/overview").status_code == 401
    assert client.get(
        "/api/metrics/overview", headers={"X-Metrics-Token": "wrong"}
    ).status_code == 401
    assert client.get(
        "/api/metrics/overview", headers={"X-Metrics-Token": "s3cret"}
    ).status_code == 200
    # Параметром запроса — чтобы можно было открыть ссылку и выгрузить CSV.
    assert client.get("/api/metrics/overview?token=s3cret").status_code == 200


def test_health_stays_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка живости нужна docker и площадке — её закрывать нельзя."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("METRICS_TOKEN", "s3cret")
    get_settings.cache_clear()
    from app.main import app

    assert TestClient(app).get("/api/health").status_code == 200


@pytest.mark.parametrize(
    "name, value, message",
    [
        ("QUESTIONS_PER_TOPIC", "два", "ожидалось число"),
        ("LLM_MAX_PARALLEL", "0", "допустимо от 1"),
        ("LLM_TIMEOUT", "-5", "допустимо от 5"),
        ("SLIDE_TOPICS", "100", "допустимо от 1 до 20"),
    ],
)
def test_bad_numbers_are_explained(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    """Ноль вопросов на тему или отрицательный таймаут дают не ошибку,
    а тихо испорченный комплект — поэтому отказ при старте и понятный текст."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat")
    monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match=message):
        get_settings()
        build_provider()


def test_thresholds_have_documented_defaults() -> None:
    """Пороги вынесены в настройки, умолчания — прежние."""
    from app.config import Settings

    s = Settings()
    assert s.questions_per_topic == 2
    assert s.slide_topics == 2
    assert s.max_clarifying_questions == 5, "чек-лист кейса: не больше пяти вопросов"
    assert s.max_reference_mb == 25


def test_cache_is_reported_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """README обещает предупреждение — с кэшем на защите генерация не настоящая."""
    from app.config import check_config

    monkeypatch.setenv("LLM_CACHE", "1")
    get_settings.cache_clear()

    assert any("LLM_CACHE=1" in w for w in check_config())


@pytest.mark.parametrize("value", ["0", "false", ""])
def test_retired_flag_switched_off_does_not_block(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Старый Makefile всегда передавал LLM_ALLOW_ANY_HOST=0 — это не повод
    отказываться от запуска: выключенный флаг ничего не открывал."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://hub.gigaschool.ru/api/v1/llm/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat/GigaChat-3-Pro")
    monkeypatch.setenv("LLM_ALLOW_ANY_HOST", value)
    get_settings.cache_clear()

    assert build_provider().name == "openai_compat"


@pytest.mark.parametrize("key", ["ВАШ_КЛЮЧ", "<ключ>", "AQVN abc def", "ключ-от-yandex"])
def test_placeholder_instead_of_key_is_explained(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """Живой случай: подставили «ВАШ_КЛЮЧ» — и каждый запрос падал с
    «'ascii' codec can't encode», по которому не понять, что дело в ключе."""
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://hub.gigaschool.ru/api/v1/llm/v1")
    monkeypatch.setenv("OPENAI_MODEL", "GigaChat/GigaChat-3-Pro")
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="текст-подсказка"):
        build_provider()


# --- Журнал и проверки допуска (разбор 20 октября) ---------------------------
# «Конфигурация ограничений по шлюзам и хостам требует дополнительного
# логирования и проверок». Теперь видно, каким правилом разрешён адрес, а
# ключ не уходит ни по адресу с паролем, ни по перенаправлению, ни через
# провайдер, созданный в обход конфигурации.


def test_rule_names_its_source() -> None:
    from app.config import host_rule

    assert host_rule("https://foundation-models.api.cloud.ru/v1") == "cloud.ru (список кейса)"
    assert host_rule("https://llm.example-platform.ru/v1", ("llm.example-platform.ru",)) == (
        "llm.example-platform.ru (LLM_EXTRA_HOSTS)"
    )
    assert host_rule("https://api.openai.com/v1") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@foundation-models.api.cloud.ru/v1",  # пароль уехал бы в журнал
        "ftp://foundation-models.api.cloud.ru/v1",
        "foundation-models.api.cloud.ru/v1",  # без схемы
    ],
)
def test_malformed_url_refuses_to_start(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", url)
    monkeypatch.setenv("OPENAI_MODEL", "m")
    with pytest.raises(ConfigError):
        build_provider()


def test_startup_log_shows_host_and_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import describe_config

    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    line = next(x for x in describe_config() if x.startswith("проверка хоста"))
    assert "foundation-models.api.cloud.ru" in line
    assert "cloud.ru (список кейса)" in line


def test_plain_http_to_remote_host_is_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import check_config

    monkeypatch.setenv("OPENAI_BASE_URL", "http://foundation-models.api.cloud.ru/v1")
    assert any("http://" in w for w in check_config())
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8080/v1")
    get_settings.cache_clear()
    assert not any("http://" in w for w in check_config())


def test_provider_refuses_foreign_host_even_without_config(caplog: pytest.LogCaptureFixture) -> None:
    from app.config import ALLOWED_LLM_HOSTS
    from app.llm.base import LLMError
    from app.llm.openai_compat import OpenAICompatProvider

    with pytest.raises(LLMError), caplog.at_level("WARNING", logger="agent.llm"):
        OpenAICompatProvider(
            base_url="https://api.openai.com/v1", api_key="k", model="m",
            allowed_hosts=ALLOWED_LLM_HOSTS,
        )
    assert "api.openai.com" in caplog.text


def test_built_provider_carries_host_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundation-models.api.cloud.ru/v1")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    provider = build_provider()
    assert provider.host == "foundation-models.api.cloud.ru"


@pytest.mark.anyio
async def test_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Шлюз ответил 307 на чужой хост — ключ туда не идёт, ошибка объясняет почему."""
    import httpx

    from app.llm.base import LLMError
    from app.llm.openai_compat import OpenAICompatProvider

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(307, headers={"location": "https://evil.example.com/v1/chat/completions"})

    real = httpx.AsyncClient

    def client(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    provider = OpenAICompatProvider(
        base_url="https://foundation-models.api.cloud.ru/v1", api_key="k", model="m",
    )
    with pytest.raises(LLMError, match="перенаправляет"):
        await provider.complete(system="s", user="u")
    assert seen == ["foundation-models.api.cloud.ru"]
