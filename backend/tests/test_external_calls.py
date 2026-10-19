"""Внешние вызовы к модели: таймауты, сетевые сбои, ключи в ошибках.

Разбор прогона 19 октября: «внешние вызовы и кеширование не всегда защищены
тайм-аутами и безопасной политикой обработки ключей», «недостаточная
обработка ошибок внешних вызовов может скрывать проблемы в боевом запуске».
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable

import httpx
import pytest

from app.llm.base import Completion, LLMError, LLMProvider, Usage, redact
from app.llm.cache import CachedProvider
from app.llm.openai_compat import OpenAICompatProvider

#: Ключи для тестов — фальшивые и собранные по частям: настоящий ключ в
#: репозитории недопустим, а целую строку нужного вида сканеры секретов
#: (GitHub push protection) справедливо примут за утечку.
KEY = "hub-" + "SECRET" + "key1234567890abcdef"
FAKE_YANDEX = "AQVN" + "fake" * 9
FAKE_OPENAI = "sk-" + "proj-" + "abcdefghijklmnopqrstuvwx"
_REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable], None]:
    def install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        def factory(*args, **kwargs):  # noqa: ANN002, ANN003
            kwargs["transport"] = httpx.MockTransport(handler)
            return _REAL_CLIENT(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

        async def instant(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", instant)

    return install


def _provider(**kwargs) -> OpenAICompatProvider:  # noqa: ANN003
    return OpenAICompatProvider(
        base_url="https://example.test/v1", api_key=KEY, model="m", **kwargs
    )


ANSWER = {"choices": [{"message": {"content": "{}"}}], "usage": {}, "model": "m"}


# --------------------------------------------------------------------- сеть


@pytest.mark.anyio
async def test_connection_failure_is_retried_then_explained(mock_http) -> None:  # noqa: ANN001
    """Раньше обрыв соединения улетал сырым исключением httpx без повтора."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    mock_http(handler)

    with pytest.raises(LLMError, match="сеть недоступна") as exc:
        await _provider(max_retries=3).complete(system="s", user="u")

    assert calls["n"] == 3, "сетевой сбой должен повторяться"
    assert "после 3 попыток" in str(exc.value)


@pytest.mark.anyio
async def test_timeout_is_retried_and_recovers(mock_http) -> None:  # noqa: ANN001
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=ANSWER)

    mock_http(handler)

    await _provider().complete(system="s", user="u")

    assert calls["n"] == 2


@pytest.mark.anyio
async def test_non_json_answer_is_explained(mock_http) -> None:  # noqa: ANN001
    mock_http(lambda request: httpx.Response(200, text="<html>502 Bad Gateway</html>"))

    with pytest.raises(LLMError, match="не JSON"):
        await _provider().complete(system="s", user="u")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status, hint",
    [(401, "ключ модели не подошёл"), (404, "проверьте OPENAI_MODEL"), (403, "нет доступа")],
)
async def test_client_errors_say_what_to_fix(mock_http, status: int, hint: str) -> None:  # noqa: ANN001
    """Человек должен понять, чинить ключ, адрес или ждать."""
    mock_http(lambda request: httpx.Response(status, text="error"))

    with pytest.raises(LLMError, match=hint):
        await _provider().complete(system="s", user="u")


def test_timeouts_are_split() -> None:
    """До недоступного шлюза не достучаться и за три минуты — это видно сразу."""
    from app.llm import openai_compat

    assert openai_compat.CONNECT_TIMEOUT <= 15
    assert openai_compat.CONNECT_TIMEOUT < _provider(timeout=180).timeout


# -------------------------------------------------------------------- ключи


@pytest.mark.anyio
async def test_key_echoed_by_gateway_does_not_leak(mock_http) -> None:  # noqa: ANN001
    """Шлюзы возвращают в ошибке то, что им прислали, — включая ключ."""
    mock_http(
        lambda request: httpx.Response(
            401, text=f"invalid header Authorization: Bearer {KEY}"
        )
    )

    with pytest.raises(LLMError) as exc:
        await _provider().complete(system="s", user="u")

    assert KEY not in str(exc.value)
    assert "***" in str(exc.value)


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Bearer {KEY}",
        f"Api-Key {FAKE_YANDEX}",
        f"ключ {FAKE_OPENAI} не подошёл",
    ],
)
def test_redact_hides_common_key_shapes(text: str) -> None:
    cleaned = redact(text)
    assert "***" in cleaned
    for secret in (KEY, FAKE_YANDEX, FAKE_OPENAI):
        assert secret not in cleaned


def test_redact_leaves_normal_text_alone() -> None:
    text = "модель перегружена, повторите через минуту"
    assert redact(text) == text


def test_api_errors_reach_the_user_without_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка генерации доходит до интерфейса — ключа в ней быть не должно."""
    from fastapi.testclient import TestClient

    import app.main as main

    class Leaky(LLMProvider):
        name = "leaky"
        model = "leaky"

        async def complete(self, **kwargs) -> Completion:  # noqa: ANN003
            raise RuntimeError(f"upstream said: Bearer {KEY}")

    monkeypatch.setattr(main, "provider", Leaky())
    response = TestClient(main.app).post("/api/sessions", json={"text": "любой запрос"})

    assert response.status_code == 502
    assert KEY not in response.text


# --------------------------------------------------------------------- кэш


class Counting(LLMProvider):
    name = "counting"
    model = "m"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs) -> Completion:  # noqa: ANN003
        self.calls += 1
        return Completion(text='{"ok": 1}', usage=Usage(prompt_tokens=1), model="m")


@pytest.mark.anyio
async def test_cache_entries_expire(tmp_path) -> None:  # noqa: ANN001
    inner = Counting()
    cache = CachedProvider(inner, tmp_path, ttl_hours=1)
    await cache.complete(system="s", user="u")

    # Состарим запись на два часа.
    entry = next(tmp_path.glob("*.json"))
    old = time.time() - 2 * 3600
    os.utime(entry, (old, old))

    await cache.complete(system="s", user="u")
    assert inner.calls == 2, "просроченная запись не должна отдаваться"


def test_cache_does_not_grow_without_limit(tmp_path) -> None:  # noqa: ANN001
    for i in range(30):
        path = tmp_path / f"{i:04d}.json"
        path.write_text('{"text": "x", "model": "m"}', encoding="utf-8")
        os.utime(path, (time.time() - 1000 + i, time.time() - 1000 + i))

    CachedProvider(Counting(), tmp_path, max_files=10)

    left = sorted(p.name for p in tmp_path.glob("*.json"))
    assert len(left) == 10
    assert left[0] == "0020.json", "удаляться должны самые старые"


@pytest.mark.anyio
async def test_cache_files_are_private(tmp_path) -> None:  # noqa: ANN001
    cache = CachedProvider(Counting(), tmp_path / "c")
    await cache.complete(system="s", user="u")

    entry = next((tmp_path / "c").glob("*.json"))
    assert entry.stat().st_mode & 0o077 == 0, "кэш доступен другим пользователям"
    assert KEY not in entry.read_text(encoding="utf-8")
