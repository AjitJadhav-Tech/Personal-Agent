"""Shared human-in-the-loop nodes.

The approval is split across two nodes on purpose: LangGraph re-runs a node from the top when it
resumes after ``interrupt()``, so creating the approval and sending the email must happen in a node
that has already completed, or every resume would send a duplicate email.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from agent.approvals import Approval, approval_link
from agent.notify import approval_email
from agent.services import get_services


def request_approval(
    state: dict[str, Any],
    config: RunnableConfig,
    *,
    kind: str,
    task: str,
    title: str,
    preview: dict[str, Any],
) -> dict[str, Any]:
    svc = get_services()
    approval = Approval.new(
        kind=kind,
        task=task,
        thread_id=config["configurable"]["thread_id"],
        title=title,
        preview=preview,
    )
    svc.store.put_approval(approval)
    link = approval_link(svc.settings.approval_base_url, approval.approval_id, svc.approval_secret)
    svc.notifier.send(*approval_email(approval, link))
    svc.store.log(task, f"Approval requested: {title}", {"approval_id": approval.approval_id})
    return {"approval_id": approval.approval_id}


def await_decision(state: dict[str, Any]) -> dict[str, Any]:
    decision = interrupt({"approval_id": state["approval_id"]})
    return {"decision": decision}
