"""Материалы, версии и предложения: модель не меняет общую программу сама."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.base import LLMProvider
from app.schemas.ir import Program, Slide
from app.schemas.review import ReviewFinding
from app.skills import load_skill
from app.store import Session


class StrictEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TopicEdit(StrictEdit):
    content: str = Field(min_length=1, max_length=50000)
    activities: str = Field(max_length=30000)


class TestEdit(StrictEdit):
    stem: str = Field(min_length=1, max_length=12000)
    options: list[str] = Field(min_length=2, max_length=20)
    correct: list[int] = Field(min_length=1)
    explanation: str = Field(max_length=12000)

    @model_validator(mode="after")
    def valid_answers(self):
        if any(not v.strip() for v in self.options) or len(set(self.options)) != len(self.options):
            raise ValueError("варианты ответа должны быть непустыми и различаться")
        if len(set(self.correct)) != len(self.correct) or any(i < 0 or i >= len(self.options) for i in self.correct):
            raise ValueError("ключ ответа должен указывать на существующие варианты")
        return self


class SeminarEdit(StrictEdit):
    title: str = Field(min_length=1, max_length=1000)
    statement: str = Field(min_length=1, max_length=30000)
    deliverable: str = Field(max_length=10000)
    criteria: list[str] = Field(max_length=30)


class LectureEdit(StrictEdit):
    title: str = Field(min_length=1, max_length=1000)
    objectives: list[str]
    timing: list[str]
    slides: list[Slide] = Field(min_length=1, max_length=100)


class AttestationEdit(StrictEdit):
    description: str = Field(min_length=1, max_length=30000)
    criteria: list[str] = Field(max_length=30)


EDIT_TYPES = {"topic": TopicEdit, "test": TestEdit, "seminar": SeminarEdit,
              "lecture": LectureEdit, "attestation": AttestationEdit}
KIND_LABELS = {"topic": "Материал темы", "test": "Тест", "seminar": "Практическое задание",
               "lecture": "Слайды", "attestation": "Итоговая аттестация"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def program_revision(program: Program) -> str:
    return digest(program.model_dump(mode="json"))


def material_items(program: Program) -> list[dict]:
    items = []
    topics = {t.id: t for t in program.all_topics()}

    def add(kind, obj, topic_id, title):
        fields = EDIT_TYPES[kind].model_fields
        body = obj.model_dump(mode="json")
        value = {k: body[k] for k in fields}
        topic = topics.get(topic_id)
        # Проверка теста устаревает и при изменении связанного объяснения.
        context = {
            "topic": topic.model_dump(mode="json") if topic else None,
            "outcomes": [o.model_dump(mode="json") for o in program.outcomes],
            "audience": program.meta.audience,
            "entry_level": program.meta.entry_level,
            "goal": program.goal,
            "preferences": program.learning_preferences.model_dump(),
            "links": body.get("outcome_ids", []),
        }
        items.append({"id": f"{kind}:{getattr(obj, 'id', 'final')}", "kind": kind,
                      "label": KIND_LABELS[kind], "title": title, "topic_id": topic_id if topic else "general",
                      "topic_label": f"{topic.code} {topic.title}" if topic else "Общие материалы",
                      "revision": digest([body, context]), "value": value, "outcome_ids": body.get("outcome_ids", [])})

    for t in program.all_topics():
        add("topic", t, t.id, f"{t.code} {t.title}")
        for q in program.test_questions:
            if q.topic_id == t.id:
                add("test", q, t.id, q.stem)
        for s in program.seminar_tasks:
            if s.topic_id == t.id:
                add("seminar", s, t.id, s.title)
        for l in program.lecture_plans:
            if l.topic_id == t.id:
                add("lecture", l, t.id, l.title)
    for kind, objects in [("test", program.test_questions), ("seminar", program.seminar_tasks), ("lecture", program.lecture_plans)]:
        for obj in objects:
            if obj.topic_id not in topics:
                add(kind, obj, None, getattr(obj, "stem", getattr(obj, "title", "")))
    add("attestation", program.final_attestation, None, "Итоговая аттестация")
    return items


def editable_value(item: dict, value: dict) -> dict:
    parsed = EDIT_TYPES[item["kind"]].model_validate(value).model_dump(mode="json")
    for key, val in parsed.items():
        if isinstance(val, str) and key in {"stem", "content", "title", "statement", "description"} and not val.strip():
            raise ValueError("текст материала не может быть пустым")
    return parsed


def apply_edit(program: Program, item: dict, value: dict) -> Program:
    """Копия гарантирует: ошибка в проверке не оставит половину правки."""
    result = program.model_copy(deep=True)
    kind, obj_id = item["id"].split(":", 1)
    collections = {"topic": result.all_topics(), "test": result.test_questions,
                   "seminar": result.seminar_tasks, "lecture": result.lecture_plans}
    target = result.final_attestation if kind == "attestation" else next(o for o in collections[kind] if o.id == obj_id)
    for key, val in value.items():
        setattr(target, key, [Slide(**s) for s in val] if key == "slides" else val)
    return Program.model_validate(result.model_dump(mode="json"))


def review_view(session: Session) -> dict:
    assert session.program is not None
    review = session.review
    items = material_items(session.program)
    by_id = {i["id"]: i for i in items}
    for item in items:
        decision = review.decisions.get(item["id"])
        item["decision"] = decision.model_dump() if decision else None
        item["status"] = (decision.status if decision.revision == item["revision"] else "stale") if decision else "todo"
        item["findings"] = [f.model_dump() for f in review.findings if f.item_id == item["id"] and f.revision == item["revision"]]
    proposals = [{**p.model_dump(), "stale": p.item_id not in by_id or by_id[p.item_id]["revision"] != p.revision} for p in review.proposals]
    revision = program_revision(session.program)
    last = review.approvals[-1] if review.approvals else None
    approval = {**last.model_dump(), "stale": last.revision != revision} if last else None
    reviewed = sum(i["status"] == "reviewed" for i in items)
    groups = list(dict.fromkeys(i["topic_id"] for i in items))
    return {"revision": revision, "items": items, "proposals": proposals,
            "decision_history": [d.model_dump() for d in review.decision_history],
            "approval": approval, "approvals": [a.model_dump() for a in review.approvals],
            "scans": {key: {**scan.model_dump(), "stale": scan.revision != digest([i["revision"] for i in items if i["topic_id"] == key])}
                      for key, scan in review.scans.items() if key in groups},
            "summary": {"total": len(items), "reviewed": reviewed,
                        "pending": sum(p.status == "pending" for p in review.proposals),
                        "needs_changes": sum(i["status"] == "needs_changes" for i in items),
                        "stale": sum(i["status"] == "stale" for i in items)}}


async def analyze(provider: LLMProvider, program: Program, items: list[dict]):
    skill = load_skill("expert_review")
    data, usage = await provider.complete_json(system=skill.system, user=skill.render_user(
        title=program.meta.title, audience=program.meta.audience,
        outcomes=json.dumps([o.model_dump() for o in program.outcomes], ensure_ascii=False),
        materials=json.dumps(items, ensure_ascii=False)), max_tokens=4000)
    if not isinstance(data.get("findings"), list):
        raise ValueError("ответ проверки не содержит списка замечаний")
    known = {i["id"]: i for i in items}
    findings = []
    for raw in data["findings"][:30]:
        if not isinstance(raw, dict) or raw.get("item_id") not in known:
            continue
        item = known[raw["item_id"]]
        if not all(isinstance(raw.get(k), str) and raw[k].strip() for k in ("title", "reason", "quote")):
            continue
        # Цитата должна существовать в материале, иначе привязка выдумана.
        def strings(v):
            if isinstance(v, str): return [v]
            if isinstance(v, list): return [s for x in v for s in strings(x)]
            if isinstance(v, dict): return [s for x in v.values() for s in strings(x)]
            return []
        if not any(raw["quote"] in s for s in strings(item["value"])):
            continue
        findings.append(ReviewFinding(item_id=item["id"], revision=item["revision"],
            title=raw["title"][:500], reason=raw["reason"][:4000], quote=raw["quote"][:3000],
            suggestion=str(raw.get("suggestion") or "")[:3000]))
    return findings, usage


async def suggest(provider: LLMProvider, program: Program, item: dict, instruction: str):
    skill = load_skill("expert_edit")
    topic = program.topic_by_id(item["topic_id"])
    data, usage = await provider.complete_json(system=skill.system, user=skill.render_user(
        title=program.meta.title, audience=program.meta.audience,
        context=json.dumps(topic.model_dump(mode="json") if topic else {}, ensure_ascii=False),
        material=json.dumps(item["value"], ensure_ascii=False), instruction=instruction), max_tokens=6000)
    return editable_value(item, data.get("replacement", {})), usage


async def explain_material(provider: LLMProvider, program: Program, item: dict, question: str):
    skill = load_skill("expert_answer")
    topic = program.topic_by_id(item["topic_id"])
    data, usage = await provider.complete_json(system=skill.system, user=skill.render_user(
        title=program.meta.title,
        context=json.dumps(topic.model_dump(mode="json") if topic else {}, ensure_ascii=False),
        material=json.dumps(item["value"], ensure_ascii=False), question=question), max_tokens=2500)
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("агент не вернул объяснение")
    return answer.strip(), usage
