"""Реестр навыков агента.

Каждый шаг пайплайна описан как отдельный навык: инструкция + шаблон входа.
Так логика переносится в GigaCowork, где навык — единица конфигурации, и так
же проще менять одну инструкцию, не трогая остальные.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

SKILLS_DIR = Path(__file__).parent


class Skill(BaseModel):
    name: str
    title: str
    description: str = ""
    system: str
    user_template: str

    def render_user(self, **kwargs: object) -> str:
        return self.user_template.format(**kwargs)


@lru_cache
def load_skill(name: str) -> Skill:
    path = SKILLS_DIR / f"{name}.yaml"
    if not path.exists():
        raise KeyError(f"навык «{name}» не найден в {SKILLS_DIR}")
    return Skill(**yaml.safe_load(path.read_text(encoding="utf-8")))


def list_skills() -> list[Skill]:
    return [load_skill(p.stem) for p in sorted(SKILLS_DIR.glob("*.yaml"))]
