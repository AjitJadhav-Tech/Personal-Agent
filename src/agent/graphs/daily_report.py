"""Emails the owner a summary of the last 24 hours. Needs no approval: it only goes to them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent import linkedin
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
    svc.notifier.send(
        *report_email(now.date().isoformat(), entries, linkedin_status=_linkedin_status(svc, now))
    )
    return {"entries": len(entries), "status": "sent"}


def _linkedin_status(svc, now: datetime) -> tuple[str, str | None]:
    """A line about the LinkedIn connection, plus the connect link when action is needed."""
    if svc.linkedin is None or not svc.settings.linkedin_client_id:
        return "LinkedIn publishing is not set up.", None
    link = linkedin.connect_link(svc.settings.approval_base_url, svc.approval_secret)
    try:
        record = svc.linkedin.record()
    except Exception as e:  # the report must still go out
        return f"Could not read the LinkedIn connection: {e}", link
    left = linkedin.days_left(record, now)
    if left is None:
        return "LinkedIn is not connected, so approved posts cannot be published.", link
    if left <= 0:
        return "The LinkedIn connection has expired. Reconnect to keep publishing.", link
    if left <= 7:
        return f"The LinkedIn connection expires in {int(left)} days. Reconnect now.", link
    return f"LinkedIn connected as {record.get('name') or 'you'} ({int(left)} days left).", None


def build() -> StateGraph:
    g = StateGraph(ReportState)
    g.add_node("send_report", send_report)
    g.add_edge(START, "send_report")
    g.add_edge("send_report", END)
    return g
