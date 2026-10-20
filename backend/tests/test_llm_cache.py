"""Кэш ответов модели: второй одинаковый запрос не должен стоить денег."""
from __future__ import annotations

import pytest

from app.llm.base import Completion, LLMProvider, Usage
from app.llm.cache import CachedProvider


class CountingProvider(LLMProvider):
    """Считает, сколько раз к нему действительно обратились."""

    name = "counting"
    model = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, system: str, user: str, temperature: float = 0.3,
                       max_tokens: int = 4000, json_mode: bool = False) -> Completion:
        self.calls += 1
        return Completion(
            text=f'{{"answer": "{user}"}}',
            usage=Usage(prompt_tokens=100, completion_tokens=50),
            model=self.model,
        )


@pytest.mark.anyio
async def test_second_identical_call_is_free(tmp_path) -> None:  # noqa: ANN001
    inner = CountingProvider()
    provider = CachedProvider(inner, tmp_path)

    first = await provider.complete(system="s", user="вопрос")
    second = await provider.complete(system="s", user="вопрос")

    assert inner.calls == 1, "второй раз к модели ходить не нужно"
    assert first.text == second.text
    assert second.usage.total == 0, "за кэш токены не начисляем"
    assert (provider.hits, provider.misses) == (1, 1)


@pytest.mark.anyio
async def test_changed_prompt_goes_to_the_model(tmp_path) -> None:  # noqa: ANN001
    """Правка навыка обязана давать новый запрос, иначе кэш законсервирует баг."""
    inner = CountingProvider()
    provider = CachedProvider(inner, tmp_path)

    await provider.complete(system="s", user="вопрос")
    await provider.complete(system="s", user="другой вопрос")
    await provider.complete(system="новая инструкция", user="вопрос")

    assert inner.calls == 3


@pytest.mark.anyio
async def test_cache_survives_restart(tmp_path) -> None:  # noqa: ANN001
    """Кэш живёт на диске: новый процесс подхватывает записанное."""
    first_run = CachedProvider(CountingProvider(), tmp_path)
    await first_run.complete(system="s", user="вопрос")

    inner = CountingProvider()
    second_run = CachedProvider(inner, tmp_path)
    result = await second_run.complete(system="s", user="вопрос")

    assert inner.calls == 0
    assert "вопрос" in result.text


@pytest.mark.anyio
async def test_broken_cache_file_does_not_break_generation(tmp_path) -> None:  # noqa: ANN001
    inner = CountingProvider()
    provider = CachedProvider(inner, tmp_path)
    await provider.complete(system="s", user="вопрос")
    # Файл побился — агент должен сходить к модели, а не упасть.
    next(tmp_path.glob("*.json")).write_text("не json", encoding="utf-8")

    result = await provider.complete(system="s", user="вопрос")

    assert inner.calls == 2
    assert result.text


# --- Межпроцессная синхронизация (разбор 20 октября) -------------------------

def test_prune_skips_when_other_process_holds_lock(tmp_path) -> None:  # noqa: ANN001
    """Пока чистит один процесс, второй каталог не трогает."""
    pytest.importorskip("fcntl")
    import subprocess
    import sys
    import time as _time

    provider = CachedProvider(CountingProvider(), tmp_path, max_files=1)
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.json").write_text("{}")

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl,sys,time;"
            f"h=open({str(tmp_path / '.prune.lock')!r},'a');"
            "fcntl.flock(h,fcntl.LOCK_EX);print('ok',flush=True);time.sleep(5)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ok"
        assert provider._prune() is False, "блокировка у другого процесса — пропускаем"
        assert len(list(tmp_path.glob("*.json"))) == 3
    finally:
        holder.kill()
        holder.wait()
    _time.sleep(0.05)
    assert provider._prune() is True
    assert len(list(tmp_path.glob("*.json"))) == 1


@pytest.mark.anyio
async def test_file_removed_by_neighbour_is_a_miss(tmp_path) -> None:  # noqa: ANN001
    inner = CountingProvider()
    provider = CachedProvider(inner, tmp_path)
    await provider.complete(system="s", user="вопрос")
    for path in tmp_path.glob("*.json"):
        path.unlink()  # «соседний процесс» почистил каталог
    await provider.complete(system="s", user="вопрос")
    assert inner.calls == 2


@pytest.mark.anyio
async def test_write_is_atomic_and_private(tmp_path) -> None:  # noqa: ANN001
    import stat

    provider = CachedProvider(CountingProvider(), tmp_path)
    await provider.complete(system="s", user="вопрос")
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert not list(tmp_path.glob("*.tmp")), "временные файлы не остаются"
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
