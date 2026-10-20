"""Кэш ответов модели на диске.

На хакатоне бюджет площадки — сотня рублей на команду, а прогон по пяти
запросам стоит десятки. При этом половина прогонов повторяет одно и то же:
меняешь одну строчку в коде и гоняешь заново весь набор. Платить за это
второй раз незачем.

Ключ кэша — модель плюс полный текст запроса, так что любое изменение
промпта или навыка даёт новый ключ и честное обращение к модели.

    LLM_CACHE=1 python3 scripts/run_open_requests.py   # первый прогон платный
    LLM_CACHE=1 python3 scripts/run_open_requests.py   # второй бесплатный

Побочная польза: записанный прогон превращается в офлайн-демо, которое
повторяет настоящие тексты модели без сети. Если на защите упадёт интернет
или кончится лимит, показывать будет что.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: блокировок через fcntl нет
    fcntl = None  # type: ignore[assignment]

from app.llm.base import Completion, LLMProvider, Usage


class CachedProvider(LLMProvider):
    """Обёртка: сначала смотрит на диск, при промахе идёт к модели."""

    def __init__(
        self,
        inner: LLMProvider,
        directory: str | Path,
        *,
        ttl_hours: int = 24,
        max_files: int = 2000,
    ) -> None:
        self.inner = inner
        self.dir = Path(directory)
        # Каталог только для владельца: в кэше тексты программ и запросов.
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ttl = ttl_hours * 3600
        self.max_files = max_files
        self.hits = 0
        self.misses = 0
        self._prune()

    @contextlib.contextmanager
    def _exclusive(self) -> Iterator[bool]:
        """Блокировка каталога между процессами.

        Кэш общий для всех процессов на машине: сервер под uvicorn с
        несколькими воркерами, прогон чек-листа рядом. Если двое чистят
        каталог одновременно, один удаляет файл, который второй сейчас
        отдаёт. Поэтому очистку делает только тот, кто взял блокировку,
        остальные её пропускают — чистка не срочная, сделает следующий.
        """
        if fcntl is None:
            yield True
            return
        handle = open(self.dir / ".prune.lock", "a")  # noqa: SIM115
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False  # чистит другой процесс
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()

    def _prune(self) -> bool:
        """Удалить просроченное и всё сверх предела, начиная со старого.

        Ключей в кэше нет — только хэш запроса в имени и текст ответа внутри.
        Но и тексты программ хранить вечно незачем. Возвращает False, если
        очистку пропустили, потому что её уже делает другой процесс.
        """
        with self._exclusive() as owner:
            if not owner:
                return False
            self._prune_locked()
            return True

    def _prune_locked(self) -> None:
        now = time.time()
        # Недописанные временные файлы от упавших процессов.
        for tmp in self.dir.glob("*.tmp"):
            with contextlib.suppress(OSError):
                if now - tmp.stat().st_mtime > 3600:
                    tmp.unlink()
        files: list[tuple[float, Path]] = []
        for path in self.dir.glob("*.json"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue  # файл удалили параллельно — тем лучше
            if now - mtime > self.ttl:
                path.unlink(missing_ok=True)
            else:
                files.append((mtime, path))
        files.sort()
        for _, path in files[: max(0, len(files) - self.max_files)]:
            path.unlink(missing_ok=True)

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"{self.inner.name}+cache"

    @property
    def model(self) -> str:  # type: ignore[override]
        return self.inner.model

    @property
    def host(self) -> str | None:
        return getattr(self.inner, "host", None)

    def _path(self, system: str, user: str, json_mode: bool) -> Path:
        key = hashlib.sha256(
            "\x00".join([self.inner.model, system, user, str(json_mode)]).encode()
        ).hexdigest()
        return self.dir / f"{key}.json"

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        json_mode: bool = False,
    ) -> Completion:
        path = self._path(system, user, json_mode)
        saved = None
        try:
            # Между проверкой и чтением файл может удалить чистка соседнего
            # процесса — это просто промах, а не ошибка.
            if time.time() - path.stat().st_mtime <= self.ttl:
                saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = None  # нет файла или битый — перезапишем
        if saved:
            self.hits += 1
            # Токены записи не начисляем: за этот ответ уже заплачено.
            return Completion(text=saved["text"], usage=Usage(), model=saved.get("model", ""))

        self.misses += 1
        result = await self.inner.complete(
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        self._write(path, {"text": result.text, "model": result.model})
        return result

    def _write(self, path: Path, data: dict) -> None:
        """Запись через временный файл и переименование: другой процесс
        увидит либо старый ответ, либо новый целиком, но не половину."""
        try:
            fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")  # создаётся с правами 0o600
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False)
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            pass  # не можем записать — не повод ронять генерацию
