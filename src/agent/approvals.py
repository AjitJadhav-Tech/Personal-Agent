"""Approval records and signed, single-use approval links.

Every outward action (publishing a post, marking a job pack ready) waits on an approval. The
approval email carries a link with an HMAC token bound to the approval id, so a forwarded or
guessed URL cannot approve anything, and the store only accepts the first decision.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

ApprovalKind = Literal["post", "job_batch"]
DecisionAction = Literal["approve", "edit", "reject"]

PENDING = "pending"
DECIDED = "decided"

# How long a request stays answerable before it expires.
EXPIRY = {"post": timedelta(hours=48), "job_batch": timedelta(days=7)}


@dataclass
class Approval:
    approval_id: str
    kind: ApprovalKind
    task: str
    thread_id: str
    title: str
    preview: dict[str, Any]
    created_at: str
    expires_at: str
    status: str = PENDING
    decision: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls, *, kind: ApprovalKind, task: str, thread_id: str, title: str, preview: dict[str, Any]
    ) -> Approval:
        now = datetime.now(UTC)
        return cls(
            approval_id=uuid.uuid4().hex,
            kind=kind,
            task=task,
            thread_id=thread_id,
            title=title,
            preview=preview,
            created_at=now.isoformat(),
            expires_at=(now + EXPIRY[kind]).isoformat(),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= datetime.fromisoformat(self.expires_at)


def sign(approval_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), approval_id.encode(), hashlib.sha256).hexdigest()


def verify(approval_id: str, token: str, secret: str) -> bool:
    return hmac.compare_digest(sign(approval_id, secret), token or "")


def approval_link(base_url: str, approval_id: str, secret: str) -> str:
    return f"{base_url}/a/{approval_id}?t={sign(approval_id, secret)}"


def normalize_decision(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a decision coming from the approval page."""
    action = raw.get("action")
    if action not in ("approve", "edit", "reject"):
        raise ValueError(f"unknown action: {action!r}")
    decision: dict[str, Any] = {"action": action}
    if action == "edit":
        text = (raw.get("text") or "").strip()
        if not text:
            raise ValueError("edit needs the edited text")
        decision["text"] = text
    if action == "reject":
        decision["reason"] = (raw.get("reason") or "").strip()
    if raw.get("selected") is not None:
        decision["selected"] = [str(s) for s in raw["selected"]]
    return decision
