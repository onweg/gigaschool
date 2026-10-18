"""Поведение при лимитах шлюза.

Шлюз площадки отвечает 429 «too many concurrent requests from this team»:
лимит общий на всю команду. Это не поломка, а штатная ситуация — агент обязан
подождать и повторить, иначе прогон по тестовым запросам падает целиком.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from app.llm.base import LLMError
from app.llm.openai_compat import OpenAICompatProvider

ANSWER = {
    "choices": [{"message": {"content": '{"ok": true}'}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "model": "test-model",
}

#: Настоящий класс сохраняем до подмены, иначе фабрика вызовет сама себя.
_REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable], None]:
    """Подменить HTTP-слой заданным обработчиком и убрать паузы backoff."""

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
        base_url="https://example.test/v1", api_key="k", model="test-model", **kwargs
    )


@pytest.mark.anyio
async def test_retries_until_gateway_lets_through(mock_http) -> None:  # noqa: ANN001
    """Два отказа подряд — на третий раз ответ принимается."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": {"code": "llm_too_many_requests"}})
        return httpx.Response(200, json=ANSWER)

    mock_http(handler)

    result = await _provider().complete(system="s", user="u")

    assert calls["n"] == 3, "должно было хватить трёх обращений"
    assert result.usage.prompt_tokens == 10


@pytest.mark.anyio
async def test_gives_up_with_clear_message(mock_http) -> None:  # noqa: ANN001
    """Если шлюз не отпускает — понятная ошибка, а не бесконечный цикл."""
    mock_http(lambda request: httpx.Response(429, text="too many concurrent requests"))

    with pytest.raises(LLMError) as exc:
        await _provider(max_retries=3).complete(system="s", user="u")

    assert "после 3 попыток" in str(exc.value)


@pytest.mark.anyio
async def test_client_error_is_not_retried(mock_http) -> None:  # noqa: ANN001
    """401 повторять бессмысленно: ключ от повторов не исправится."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    mock_http(handler)

    with pytest.raises(LLMError, match="401"):
        await _provider().complete(system="s", user="u")
    assert calls["n"] == 1, "повторять ошибку авторизации не нужно"


@pytest.mark.anyio
async def test_parallel_calls_respect_the_gate(mock_http) -> None:  # noqa: ANN001
    """Одновременных запросов не больше лимита — иначе шлюз снова скажет 429."""
    state = {"now": 0, "peak": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0)  # дать другим задачам шанс стартовать
        state["now"] -= 1
        return httpx.Response(200, json=ANSWER)

    mock_http(handler)
    provider = _provider(max_parallel=2)

    await asyncio.gather(
        *(provider.complete(system="s", user=f"u{i}") for i in range(6))
    )

    assert state["peak"] <= 2, f"одновременно шло {state['peak']} запросов"
