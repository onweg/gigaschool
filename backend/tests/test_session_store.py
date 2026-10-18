"""Хранилище сессий: целостность файлов и границы каталога данных.

Проверяющие отметили: «SessionStore сохраняет состояния в файловом хранилище
без механизмов блокировок или транзакций, создавая риск повреждения состояния
при параллельной работе» и «защита файлового хранилища сессий».
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.store import Session, SessionStore


@pytest.fixture
def store(tmp_path) -> SessionStore:  # noqa: ANN001
    return SessionStore(str(tmp_path))


def test_save_and_load_roundtrip(store: SessionStore, tmp_path) -> None:  # noqa: ANN001
    session = store.create()
    session.log("intake", "проверка")
    store.save(session)

    fresh = SessionStore(str(tmp_path))
    loaded = fresh.get(session.id)

    assert loaded is not None
    assert loaded.id == session.id
    assert loaded.events[-1].message == "проверка"


def test_write_is_atomic(store: SessionStore, tmp_path) -> None:  # noqa: ANN001
    """После записи в каталоге нет ни временных, ни недописанных файлов."""
    session = store.create()
    store.save(session)
    store.save(session)

    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [f"{session.id}.json"], files
    # Файл читается целиком — значит переименование произошло после записи.
    json.loads((tmp_path / f"{session.id}.json").read_text(encoding="utf-8"))


def test_broken_file_does_not_crash_the_service(store: SessionStore, tmp_path) -> None:  # noqa: ANN001
    """Повреждённый файл — как отсутствующая сессия, а не 500 на весь сервис."""
    session = store.create()
    store.save(session)
    (tmp_path / f"{session.id}.json").write_text("{недописанный", encoding="utf-8")

    fresh = SessionStore(str(tmp_path))
    assert fresh.get(session.id) is None


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "a" * 200,
        "../metrics.sqlite3",
        "",
        "ЖЖЖЖЖЖЖЖЖЖЖЖ",
    ],
)
def test_path_traversal_is_refused(store: SessionStore, bad_id: str, tmp_path) -> None:  # noqa: ANN001
    """Идентификатор приходит из URL и подставляется в имя файла.

    Без проверки формы «../../» уводит чтение и запись за пределы каталога.
    """
    assert store.get(bad_id) is None

    forged = Session()
    forged.id = bad_id
    store.save(forged)  # не должно ничего записать и не должно упасть

    written = [p.name for p in tmp_path.iterdir()]
    assert all(name.endswith(".json") and len(name) == 17 for name in written), written


@pytest.mark.anyio
async def test_parallel_saves_keep_the_file_readable(store: SessionStore, tmp_path) -> None:  # noqa: ANN001
    """Одновременные записи не оставляют файл в промежуточном состоянии."""
    session = store.create()

    async def write(n: int) -> None:
        async with store.lock(session.id):
            session.log("content", f"событие {n}")
            store.save(session)

    await asyncio.gather(*(write(i) for i in range(20)))

    data = json.loads((tmp_path / f"{session.id}.json").read_text(encoding="utf-8"))
    assert len(data["events"]) == 20
