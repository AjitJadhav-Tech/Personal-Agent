"""Opportunities the owner received elsewhere (recruiter emails, LinkedIn messages, referrals).

The owner pastes the message into the web app; the agent reads it, drafts a reply in their voice
(every claim checked against the master profile), optionally builds a tailored resume and cover
letter, and emails it all to the owner. Nothing is sent to anyone else: the owner sends the reply.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.notify import opportunity_email
from agent.profile import load_master_profile
from agent.services import get_services

TASK = "opportunity"


class OpportunityState(TypedDict, total=False):
    id: str
    created_at: str
    text: str
    link: str
    documents: bool
    status: str


def new_record(text: str, link: str = "", documents: bool = False) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(UTC).isoformat(),
        "text": text,
        "link": link,
        "documents": documents,
        "status": "drafting",
    }


def prepare(state: OpportunityState) -> OpportunityState:
    from agent.tailoring import analyze_opportunity, draft_reply

    svc = get_services()
    record = {k: state.get(k) for k in ("id", "created_at", "text", "link", "documents")}
    record["id"] = record["id"] or uuid.uuid4().hex[:12]
    record["created_at"] = record["created_at"] or datetime.now(UTC).isoformat()
    text = state["text"] + (f"\n\nLink: {state['link']}" if state.get("link") else "")
    try:
        profile = load_master_profile()
        info = analyze_opportunity(svc.drafter, text)
        reply = draft_reply(svc.drafter, profile, text, info)
        attachments = _documents(svc, profile, info, text) if state.get("documents") else []
        record.update(info=info, reply=reply, status="ready")
        svc.notifier.send(*opportunity_email(record), attachments=attachments)
        label = " at ".join(x for x in (info["title"], info["company"]) if x) or "an opportunity"
        svc.store.log(TASK, f"Reply drafted for {label}; check your email")
    except Exception as e:
        record.update(status="failed", error=str(e)[:500])
        svc.store.put_opportunity(record)
        svc.store.log(TASK, f"Could not draft a reply: {e}")
        raise
    svc.store.put_opportunity(record)
    return {"status": "ready"}


def _documents(svc, profile: dict[str, Any], info: dict[str, Any], text: str) -> list:
    from agent.documents import cover_letter_pdf, resume_pdf
    from agent.tailoring import tailor_resume, write_cover_letter

    job = {
        "id": "opportunity",
        "title": info["title"] or "the role",
        "company": info["company"] or "your team",
        "location": info["location"],
        "description": text,
    }
    resume = tailor_resume(svc.drafter, profile, job)
    letter = write_cover_letter(svc.drafter, profile, job)
    name = profile["basics"]["name"].replace(" ", "_")
    company = re.sub(r"\W+", "_", info["company"] or "Opportunity").strip("_")
    return [
        (f"{name}_Resume_{company}.pdf", resume_pdf(resume), "application/pdf"),
        (f"{name}_Cover_Letter_{company}.pdf", cover_letter_pdf(letter), "application/pdf"),
    ]


def build() -> StateGraph:
    g = StateGraph(OpportunityState)
    g.add_node("prepare", prepare)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", END)
    return g
