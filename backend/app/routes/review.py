"""Рабочие экраны проверки. Роли интерфейса не заменяют авторизацию."""
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from app.core.validate import validate_program
from app.llm.metered import current_session
from app.pipeline.expert_review import (
    analyze, apply_edit, digest, editable_value, explain_material, material_items, program_revision, review_view, suggest,
)
from app.schemas.review import ApprovalDecision, EditProposal, ReviewDecision, ReviewDecisionRecord, ReviewScan
from app.store import Stage, store


class VersionIn(BaseModel):
    revision: str


class DecisionIn(VersionIn):
    item_id: str
    status: Literal["reviewed", "deferred", "needs_changes"]
    author: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=5000)


class ProposalIn(VersionIn):
    item_id: str
    after: dict[str, Any]
    author: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=5000)


class BatchItemIn(VersionIn):
    item_id: str


class BatchDecisionIn(BaseModel):
    items: list[BatchItemIn] = Field(min_length=1, max_length=2000)
    author: str = Field(min_length=1, max_length=120)


class SuggestIn(VersionIn):
    item_id: str
    instruction: str = Field(min_length=1, max_length=5000)


class ScanIn(BaseModel):
    topic_id: str


class ResolveIn(BaseModel):
    action: Literal["accept", "reject"]


class ApprovalIn(VersionIn):
    decision: Literal["approved", "returned"]
    author: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=5000)


def review_router(provider, get_session, state, safe):
    router = APIRouter(prefix="/api/sessions/{session_id}/review")

    def available(session_id, mutate=False):
        s = get_session(session_id)
        if s.program is None:
            raise HTTPException(409, "сначала соберите программу")
        if mutate and (s.phase or s.stage == Stage.CONTENT or store.lock(session_id).locked()):
            raise HTTPException(409, "материалы сейчас обновляются; повторите после завершения")
        return s

    def item_for(s, item_id, revision):
        item = next((i for i in material_items(s.program) if i["id"] == item_id), None)
        if item is None:
            raise HTTPException(404, "материал больше не существует")
        if item["revision"] != revision:
            raise HTTPException(409, "материал изменился; обновите экран и проверьте новую версию")
        return item

    def validate_edit(item, value):
        try:
            return editable_value(item, value)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, f"проверьте текст, варианты и ключи ответа: {exc}") from None

    def save_decision(s, item_id, decision):
        previous = s.review.decisions.get(item_id)
        if previous:
            s.review.decision_history.append(ReviewDecisionRecord(item_id=item_id, **previous.model_dump()))
        s.review.decisions[item_id] = decision

    @router.get("")
    async def get_review(session_id: str):
        return review_view(available(session_id))

    @router.post("/decisions")
    async def decision(session_id: str, payload: DecisionIn):
        s = available(session_id, True)
        if not payload.author.strip():
            raise HTTPException(422, "укажите имя эксперта")
        if payload.status == "needs_changes" and not payload.comment.strip():
            raise HTTPException(422, "добавьте замечание для методиста")
        item_for(s, payload.item_id, payload.revision)
        save_decision(s, payload.item_id, ReviewDecision(**payload.model_dump(exclude={"item_id"})))
        s.log("review", f"{payload.author}: {payload.item_id} — {payload.status}")
        store.save(s)
        return review_view(s)

    @router.post("/decisions/batch")
    async def batch_decision(session_id: str, payload: BatchDecisionIn):
        s = available(session_id, True)
        if not payload.author.strip():
            raise HTTPException(422, "укажите имя эксперта")
        ids = [i.item_id for i in payload.items]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "список содержит повторяющиеся материалы")
        # Проверяем весь набор до первой записи: либо согласовано всё выбранное,
        # либо ни один статус не изменён. Скрытые/новые материалы не добавляем.
        current = {i["id"]: i for i in material_items(s.program)}
        for item in payload.items:
            if item.item_id not in current or current[item.item_id]["revision"] != item.revision:
                raise HTTPException(409, "материалы изменились; обновите список и повторите согласование")
        for item in payload.items:
            previous = s.review.decisions.get(item.item_id)
            if previous and previous.status == "reviewed" and previous.revision == item.revision:
                continue
            save_decision(s, item.item_id, ReviewDecision(revision=item.revision, status="reviewed",
                author=payload.author.strip(), method="batch"))
        s.log("review", f"{payload.author.strip()}: согласовано пакетом материалов — {len(ids)}")
        store.save(s)
        return review_view(s)

    @router.post("/findings/{finding_id}/dismiss")
    async def dismiss(session_id: str, finding_id: str, payload: VersionIn):
        s = available(session_id, True)
        f = next((f for f in s.review.findings if f.id == finding_id), None)
        if f is None:
            raise HTTPException(404, "замечание не найдено")
        item_for(s, f.item_id, payload.revision)
        if f.revision != payload.revision:
            raise HTTPException(409, "замечание относится к прошлой версии")
        f.dismissed = True
        store.save(s)
        return review_view(s)

    @router.post("/scan")
    async def scan(session_id: str, payload: ScanIn):
        s = available(session_id, True)
        items = [i for i in material_items(s.program) if i["topic_id"] == payload.topic_id]
        if not items:
            raise HTTPException(404, "тема не найдена")
        revision = digest([i["revision"] for i in items])
        current_session.set(s.id)
        # Во время ожидания модели методист может работать; результат применяется
        # только к неизменившемуся снимку, без удержания блокировки на минуты.
        try:
            findings, usage = await analyze(provider, s.program.model_copy(deep=True), items)
        except Exception as exc:
            raise HTTPException(502, f"не удалось проверить тему: {safe(exc)}") from exc
        s.usage.add(usage)
        store.save(s)
        s = available(session_id, True)
        current = [i for i in material_items(s.program) if i["topic_id"] == payload.topic_id]
        if revision != digest([i["revision"] for i in current]):
            raise HTTPException(409, "тема изменилась во время анализа; запустите проверку ещё раз")
        ids = {i["id"] for i in current}
        s.review.findings = [f for f in s.review.findings if f.item_id not in ids] + findings
        s.review.scans[payload.topic_id] = ReviewScan(revision=revision, offline=provider.name.startswith("mock"))
        s.log("review", f"Анализ темы {payload.topic_id}: предположений для эксперта — {len(findings)}")
        store.save(s)
        return review_view(s)

    @router.post("/suggest")
    async def suggestion(session_id: str, payload: SuggestIn):
        s = available(session_id, True)
        item = item_for(s, payload.item_id, payload.revision)
        current_session.set(s.id)
        try:
            replacement, usage = await suggest(provider, s.program.model_copy(deep=True), item, payload.instruction)
        except Exception as exc:
            raise HTTPException(502, f"не удалось подготовить правку: {safe(exc)}") from exc
        s.usage.add(usage)
        store.save(s)
        s = available(session_id, True)
        item_for(s, payload.item_id, payload.revision)
        return {"replacement": replacement, "offline": provider.name.startswith("mock")}

    @router.post("/ask")
    async def ask(session_id: str, payload: SuggestIn):
        s = available(session_id, True)
        item = item_for(s, payload.item_id, payload.revision)
        current_session.set(s.id)
        try:
            answer, usage = await explain_material(provider, s.program.model_copy(deep=True), item, payload.instruction)
        except Exception as exc:
            raise HTTPException(502, f"не удалось получить объяснение: {safe(exc)}") from exc
        s.usage.add(usage)
        store.save(s)
        s = available(session_id, True)
        item_for(s, payload.item_id, payload.revision)
        return {"answer": answer}

    @router.post("/proposals")
    async def proposal(session_id: str, payload: ProposalIn):
        s = available(session_id, True)
        item = item_for(s, payload.item_id, payload.revision)
        after = validate_edit(item, payload.after)
        if not payload.author.strip():
            raise HTTPException(422, "укажите имя эксперта")
        if after == item["value"]:
            raise HTTPException(422, "правка не содержит изменений")
        if any(p.status == "pending" and p.item_id == item["id"] and p.revision == item["revision"] and p.after == after for p in s.review.proposals):
            raise HTTPException(409, "это предложение уже отправлено методисту")
        s.review.proposals.append(EditProposal(item_id=item["id"], title=item["title"], revision=item["revision"],
            before=item["value"], after=after, author=payload.author.strip(), comment=payload.comment))
        save_decision(s, item["id"], ReviewDecision(revision=item["revision"], status="needs_changes",
            author=payload.author.strip(), comment=payload.comment or "Предложена правка материала"))
        s.log("review", f"{payload.author}: предложена правка «{item['title'][:100]}»")
        store.save(s)
        return review_view(s)

    @router.post("/proposals/{proposal_id}/resolve")
    async def resolve(session_id: str, proposal_id: str, payload: ResolveIn):
        s = available(session_id, True)
        p = next((p for p in s.review.proposals if p.id == proposal_id), None)
        if p is None:
            raise HTTPException(404, "предложение не найдено")
        if p.status != "pending":
            raise HTTPException(409, "решение по правке уже принято")
        if payload.action == "accept":
            item = item_for(s, p.item_id, p.revision)
            after = validate_edit(item, p.after)
            s.program = apply_edit(s.program, item, after)
            s.report = validate_program(s.program, stage="skeleton" if s.stage == Stage.SKELETON else "full")
            p.status = "accepted"
        else:
            p.status = "rejected"
        from app.schemas.review import now
        p.decided_at = now()
        s.log("review", f"Методист: правка «{p.title[:100]}» — {p.status}")
        store.save(s)
        return {"review": review_view(s), "session": state(s)}

    @router.post("/approval")
    async def approve(session_id: str, payload: ApprovalIn):
        s = available(session_id, True)
        if payload.revision != program_revision(s.program):
            raise HTTPException(409, "программа изменилась; ознакомьтесь с новой версией")
        if not payload.author.strip():
            raise HTTPException(422, "укажите имя утверждающего")
        if payload.decision == "returned" and not payload.comment.strip():
            raise HTTPException(422, "укажите причину возврата")
        if payload.decision == "approved":
            if s.stage != Stage.READY:
                raise HTTPException(409, "сначала завершите сборку комплекта")
            s.report = validate_program(s.program)
            if s.report.errors:
                raise HTTPException(409, "исправьте ошибки автопроверки перед согласованием")
        s.review.approvals.append(ApprovalDecision(**payload.model_dump(),
            program_title=s.program.meta.title, review_summary=review_view(s)["summary"]))
        s.log("approval", f"{payload.author}: {payload.decision}. {payload.comment}")
        store.save(s)
        return review_view(s)

    @router.get("/approval-record.docx")
    async def approval_record(session_id: str):
        from app.render.approval_record import render_approval_record
        s = available(session_id)
        if not s.review.approvals:
            raise HTTPException(409, "решение руководителя ещё не сохранено")
        decision = s.review.approvals[-1]
        return Response(render_approval_record(s.id, decision, program_revision(s.program)),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": 'attachment; filename="approval-record.docx"'})

    return router
