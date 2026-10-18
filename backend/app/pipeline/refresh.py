"""Актуализация действующей программы: поиск устаревших тем и инструментов.

Требование кейса (docs/case-task.md:50, «Сверх плана»): «Актуализация старой
программы с поиском устаревших тем и инструментов».

Честная граница возможностей. Устаревание модель определяет по своей памяти,
а она имеет границу и не всегда точна — реестра устаревших технологий, в
отличие от реестра профстандартов, не существует. Поэтому агент не выносит
приговор, а помечает подозрительное, объясняет причину и предлагает замену.
Решает методист — как и везде в этом кейсе.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.llm.base import LLMProvider, Usage
from app.pipeline.skeleton import as_dicts
from app.schemas.ir import Program
from app.skills import load_skill

#: Насколько срочно. Словами, потому что методист читает отчёт, а не коды.
SEVERITY_WORDS = {
    "outdated": "устарело",
    "aging": "стареет",
    "missing": "не хватает",
}
#: Порядок вывода: сначала то, что сломается прямо сейчас.
SEVERITY_ORDER = {"outdated": 0, "aging": 1, "missing": 2}


class RefreshFinding(BaseModel):
    """Одна находка актуализации."""

    topic_code: str = ""
    what: str
    why: str = ""
    replacement: str = ""
    severity: str = "aging"

    @property
    def word(self) -> str:
        return SEVERITY_WORDS.get(self.severity, "проверить")


class RefreshReport(BaseModel):
    findings: list[RefreshFinding] = Field(default_factory=list)
    verdict: str = ""

    @property
    def outdated(self) -> list[RefreshFinding]:
        return [f for f in self.findings if f.severity == "outdated"]

    @property
    def missing(self) -> list[RefreshFinding]:
        return [f for f in self.findings if f.severity == "missing"]


def program_content(program: Program) -> str:
    """Содержание программы для проверки на устаревание.

    Нужны темы и то, что внутри: именно там живут версии библиотек и названия
    сервисов. Часы и формы контроля не передаём — актуализация их не трогает.
    """
    lines = [f"Цель: {program.goal}"] if program.goal else []
    for module in program.modules:
        lines.append(f"{module.index}. {module.title}")
        for topic in module.topics:
            lines.append(f"  {topic.code} {topic.title}")
            if topic.content:
                lines.append(f"     {topic.content}")
    if program.sources:
        lines.append("Литература и источники:")
        lines += [f"  {s.citation}" for s in program.sources]
    if program.software:
        lines.append("Программное обеспечение: " + ", ".join(program.software))
    return "\n".join(lines)


def content_from_text(text: str, limit: int = 8000) -> str:
    """Содержание из разобранного файла старой программы.

    Методист приносит .docx, которого в системе никогда не было, — разбирать
    его в полноценную программу незачем, для поиска устаревшего хватает текста.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)
    return joined[:limit]


async def find_outdated(
    provider: LLMProvider,
    content: str,
    *,
    title: str = "",
    direction: str = "",
    audience: str = "",
    year: str = "",
) -> tuple[RefreshReport, Usage]:
    """Найти устаревшее. Возвращает отчёт и расход токенов."""
    if not content.strip():
        raise ValueError("нечего проверять: содержание программы пустое")

    skill = load_skill("refresh")
    data, usage = await provider.complete_json(
        system=skill.system,
        user=skill.render_user(
            title=title or "—",
            direction=direction or "—",
            audience=audience or "—",
            year=year or "не указан",
            content_block=content,
        ),
        max_tokens=3000,
    )

    findings: list[RefreshFinding] = []
    for raw in as_dicts(data.get("findings"), key="what"):
        what = str(raw.get("what", "")).strip()
        if not what:
            continue
        severity = str(raw.get("severity", "aging")).strip().lower()
        findings.append(
            RefreshFinding(
                topic_code=str(raw.get("topic_code", "") or "").strip(),
                what=what,
                why=str(raw.get("why", "") or "").strip(),
                replacement=str(raw.get("replacement", "") or "").strip(),
                severity=severity if severity in SEVERITY_WORDS else "aging",
            )
        )
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))

    return RefreshReport(
        findings=findings,
        verdict=str(data.get("verdict", "") or "").strip(),
    ), usage
