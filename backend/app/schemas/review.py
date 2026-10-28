"""Совместная проверка хранится отдельно от программы и не блокирует экспорт."""
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now() -> str:
    return datetime.now(UTC).isoformat()


class ReviewDecision(BaseModel):
    revision: str
    status: Literal["reviewed", "deferred", "needs_changes"]
    author: str
    comment: str = ""
    at: str = Field(default_factory=now)


class ReviewFinding(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    item_id: str
    revision: str
    title: str
    reason: str
    quote: str = ""
    suggestion: str = ""
    dismissed: bool = False


class ReviewScan(BaseModel):
    revision: str
    at: str = Field(default_factory=now)
    offline: bool = False


class EditProposal(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    item_id: str
    title: str
    revision: str
    before: dict[str, Any]
    after: dict[str, Any]
    author: str
    comment: str = ""
    status: Literal["pending", "accepted", "rejected"] = "pending"
    at: str = Field(default_factory=now)
    decided_at: str = ""


class ApprovalDecision(BaseModel):
    revision: str
    decision: Literal["approved", "returned"]
    author: str
    comment: str = ""
    at: str = Field(default_factory=now)


class ReviewState(BaseModel):
    decisions: dict[str, ReviewDecision] = Field(default_factory=dict)
    findings: list[ReviewFinding] = Field(default_factory=list)
    scans: dict[str, ReviewScan] = Field(default_factory=dict)
    proposals: list[EditProposal] = Field(default_factory=list)
    approvals: list[ApprovalDecision] = Field(default_factory=list)
