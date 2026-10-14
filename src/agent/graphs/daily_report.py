"""Emails the owner a summary of the last 24 hours. Needs no approval: it only goes to them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent.notify import report_email
from agent.services import get_services

TASK = "daily_report"


class ReportState(TypedDict, total=False):
    entries: int
    status: str


def send_report(state: ReportState) -> ReportState:
    svc = get_services()
    now = datetime.now(UTC)
    entries = sorted(svc.store.activity_since(now - timedelta(days=1)), key=lambda e: e["at"])
    svc.notifier.send(*report_email(now.date().isoformat(), entries))
    return {"entries": len(entries), "status": "sent"}


def build() -> StateGraph:
    g = StateGraph(ReportState)
    g.add_node("send_report", send_report)
    g.add_edge(START, "send_report")
    g.add_edge("send_report", END)
    return g
