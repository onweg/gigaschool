"""Переменные окружения доходят до контейнера.

Живой случай: настройка адреса модели была в `.env` на сервере и читалась в
`config.py`, но `docker-compose.yml` его не пробрасывал — compose передаёт
только то, что перечислено в `environment`. Контейнер не стартовал, а в
`.env` всё выглядело правильно.

Тест сверяет два списка, чтобы следующая настройка не потерялась так же.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "backend" / "app" / "config.py"
COMPOSE = ROOT / "docker-compose.yml"

#: Задаются внутри compose жёстко, из окружения их брать не нужно.
FIXED_IN_COMPOSE = {"DATA_DIR"}


def _variables_read_by_code() -> set[str]:
    """Имена из каждого вызова os.getenv/_env, включая запасные варианты.

    `_env("LLM_BASE_URL", "OPENAI_BASE_URL")` читает обе переменные, поэтому
    обе должны доходить до контейнера.
    """
    text = CONFIG.read_text(encoding="utf-8")
    names: set[str] = set()
    for call in re.findall(r"(?:os\.getenv|_env)\(([^)]*)\)", text, re.S):
        names.update(re.findall(r'"([A-Z][A-Z0-9_]*)"', call))
    return names


def _variables_passed_by_compose() -> set[str]:
    text = COMPOSE.read_text(encoding="utf-8")
    return set(re.findall(r"^\s{6}([A-Z][A-Z0-9_]*):", text, re.M))


def test_compose_passes_every_setting() -> None:
    missing = _variables_read_by_code() - _variables_passed_by_compose() - FIXED_IN_COMPOSE
    assert not missing, (
        f"docker-compose.yml не передаёт в контейнер: {sorted(missing)}. "
        f"Настройка будет лежать в .env и молча не работать"
    )


def test_compose_does_not_pass_unknown_settings() -> None:
    """Обратная сверка: переменная в compose, которую никто не читает, — мусор."""
    extra = _variables_passed_by_compose() - _variables_read_by_code() - FIXED_IN_COMPOSE
    assert not extra, f"в compose есть лишние переменные: {sorted(extra)}"
