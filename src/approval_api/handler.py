"""Approval page behind API Gateway.

GET  /a/{id}?t=<token>  shows the item with Approve / Edit / Reject controls. It changes nothing,
                        because mail scanners (including Outlook Safe Links) open links in emails.
POST /a/{id}            records the decision once, then resumes the paused run asynchronously.
"""

from __future__ import annotations

import base64
import json
import os
from html import escape
from typing import Any
from urllib.parse import parse_qs

from agent.approvals import Approval, normalize_decision, verify
from agent.services import get_services
from agent.store import AlreadyDecided


def handler(event: dict[str, Any], context: Any = None, *, resume=None) -> dict[str, Any]:
    svc = get_services()
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    approval_id = (event.get("pathParameters") or {}).get("id", "")
    approval = svc.store.get_approval(approval_id)
    if approval is None:
        return _page(404, "Not found", "<p>This approval request does not exist.</p>")

    if method == "GET":
        token = (event.get("queryStringParameters") or {}).get("t", "")
        if not verify(approval_id, token, svc.approval_secret):
            return _page(403, "Link not valid", "<p>This link is not valid.</p>")
        return _page(200, approval.title, _review_form(approval, token))

    form = _parse_form(event)
    if not verify(approval_id, form.get("t", ""), svc.approval_secret):
        return _page(403, "Link not valid", "<p>This link is not valid.</p>")
    if approval.is_expired():
        return _page(410, "Expired", "<p>This request expired. The agent will ask again.</p>")
    try:
        decision = normalize_decision(
            {
                "action": form.get("action"),
                "text": form.get("text"),
                "reason": form.get("reason"),
                "selected": form.get("selected_list") if approval.kind == "job_batch" else None,
            }
        )
        if (
            decision["action"] == "edit"
            and decision["text"] == approval.preview.get("text", "").strip()
        ):
            decision = {"action": "approve"}
        approval = svc.store.decide(approval_id, decision)
    except ValueError as e:
        return _page(400, "Check your answer", f"<p>{escape(str(e))}</p>")
    except AlreadyDecided:
        return _page(409, "Already decided", "<p>This request was already answered.</p>")

    (resume or _resume_async)(approval)
    svc.store.log(approval.task, f"You chose '{decision['action']}' for: {approval.title}")
    return _page(
        200,
        "Thanks",
        f"<p>Recorded: <b>{escape(decision['action'])}</b>. The agent is continuing now.</p>",
    )


def _resume_async(approval: Approval) -> None:
    import boto3

    boto3.client("lambda").invoke(
        FunctionName=os.environ["RUNNER_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps(
            {"task": approval.task, "thread_id": approval.thread_id, "decision": approval.decision}
        ).encode(),
    )


def _parse_form(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    raw = parse_qs(body, keep_blank_values=True)
    form: dict[str, Any] = {k: v[0] for k, v in raw.items()}
    form["selected_list"] = raw.get("selected", [])
    return form


def _review_form(approval: Approval, token: str) -> str:
    if approval.kind == "job_batch":
        rows = "".join(
            f'<label class="job"><input type="checkbox" name="selected" value="{escape(j["id"])}" '
            f"checked> <b>{escape(j['title'])}</b> at {escape(j['company'])} "
            f"({escape(j.get('location', ''))}), match {j.get('score', '?')} "
            f'<a href="{escape(j["url"])}" target="_blank" rel="noopener">view</a></label>'
            for j in approval.preview.get("jobs", [])
        )
        body = f"<p>Untick any job you don't want an apply pack for.</p>{rows}"
        edit = ""
    else:
        text = escape(approval.preview.get("text", ""))
        body = "<p>Edit the text below if needed, then choose.</p>"
        edit = f'<textarea name="text" rows="14">{text}</textarea>'
    status = ""
    if approval.status != "pending":
        status = (
            f"<p class=note>Already answered: {escape(approval.decision.get('action', ''))}</p>"
        )
    return f"""{status}
<form method="post">
<input type="hidden" name="t" value="{escape(token)}">
{body}{edit}
<div class="row">
<button name="action" value="{"edit" if edit else "approve"}" class="primary">Approve</button>
<input name="reason" placeholder="Reason (optional, helps the agent learn)">
<button name="action" value="reject">Reject</button>
</div></form>"""


def _page(status: int, title: str, inner: str) -> dict[str, Any]:
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:680px;margin:24px auto;padding:0 16px}}
textarea{{width:100%;font:inherit}}.row{{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}}
.row input{{flex:1;min-width:160px}}button{{padding:8px 16px}}.primary{{background:#0a66c2;color:#fff;border:0;border-radius:6px}}
.job{{display:block;margin:8px 0}}.note{{color:#a15c00}}</style></head>
<body><h2>{escape(title)}</h2>{inner}</body></html>"""
    return {
        "statusCode": status,
        "headers": {
            "content-type": "text/html; charset=utf-8",
            "cache-control": "no-store",
            "x-robots-tag": "noindex",
        },
        "body": html,
    }
