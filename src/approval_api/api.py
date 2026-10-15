"""JSON API for the web app. API Gateway checks the Cognito sign-in (JWT) before these run;
the handler refuses any request that arrives without verified claims, as a second lock."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from agent import linkedin
from agent.approvals import approval_link
from agent.opportunities import new_record

RUN_TASKS = ("job_hunt", "daily_report")
TEXT_LIMIT = 20000


def handle(event: dict[str, Any], svc, start_run) -> dict[str, Any]:
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims")
    if not claims:
        return _json(401, {"error": "sign in required"})
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    route = event.get("rawPath", "").removeprefix("/api").rstrip("/") or "/"
    base_url = f"https://{event.get('requestContext', {}).get('domainName', '')}"
    try:
        body = _body(event) if method == "POST" else {}
    except ValueError:
        return _json(400, {"error": "invalid JSON"})

    if (method, route) == ("GET", "/status"):
        return _json(200, _status(svc, base_url))
    if (method, route) == ("GET", "/activity"):
        days = min(int((event.get("queryStringParameters") or {}).get("days", "3")), 14)
        since = datetime.now(UTC) - timedelta(days=days)
        entries = sorted(svc.store.activity_since(since), key=lambda e: e["at"], reverse=True)
        return _json(200, {"entries": [_entry(e) for e in entries[:200]]})
    if (method, route) == ("GET", "/opportunities"):
        return _json(200, {"opportunities": svc.store.list_opportunities(20)})

    if (method, route) == ("POST", "/posts"):
        topic = str(body.get("topic") or "").strip()
        if not topic:
            return _json(400, {"error": "Add a topic for the post."})
        notes = str(body.get("notes") or "").strip()[:TEXT_LIMIT]
        start_run({"task": "content", "input": {"topic": topic[:300], "notes": notes}})
        svc.store.log("app", f"You asked for a post about: {topic[:300]}")
        return _json(202, {"message": "Drafting your post. The approval email arrives shortly."})
    if (method, route) == ("POST", "/opportunities"):
        text = str(body.get("text") or "").strip()
        if len(text) < 20:
            return _json(400, {"error": "Paste the full message or job post."})
        record = new_record(
            text[:TEXT_LIMIT],
            str(body.get("link") or "").strip()[:500],
            bool(body.get("documents")),
        )
        svc.store.put_opportunity(record)
        start_run({"task": "opportunity", "input": record})
        svc.store.log("app", "You added an opportunity; drafting a reply")
        return _json(202, {"opportunity": record})
    if (method, route) == ("POST", "/run"):
        task = body.get("task")
        if task not in RUN_TASKS:
            return _json(400, {"error": f"task must be one of {list(RUN_TASKS)}"})
        start_run({"task": task})
        svc.store.log("app", f"You started: {task}")
        return _json(202, {"message": "Started. Results come by email."})
    if (method, route) == ("POST", "/pause"):
        paused = bool(body.get("paused"))
        svc.set_paused(paused)
        svc.store.log("app", "You paused the agent" if paused else "You resumed the agent")
        return _json(200, {"paused": paused})
    return _json(404, {"error": "not found"})


def _status(svc, base_url: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    connected, days = False, None
    if svc.linkedin is not None and svc.settings.linkedin_client_id:
        try:
            left = linkedin.days_left(svc.linkedin.record(), now)
        except Exception:
            left = None
        connected, days = (left is not None and left > 0), (int(left) if left else None)
    return {
        "paused": svc.is_paused(),
        "dry_run": svc.settings.dry_run,
        "linkedin": {
            "connected": connected,
            "days_left": days,
            "connect_url": linkedin.connect_link(base_url, svc.approval_secret),
        },
        "pending": [
            {
                "id": a.approval_id,
                "kind": a.kind,
                "title": a.title,
                "created_at": a.created_at,
                "expires_at": a.expires_at,
                "url": approval_link(base_url, a.approval_id, svc.approval_secret),
            }
            for a in svc.store.pending_approvals()
        ],
    }


def _entry(e: dict[str, Any]) -> dict[str, Any]:
    return {"at": e["at"], "kind": e["kind"], "message": e["message"]}


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("expected an object")
    return body


def _json(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(payload, default=str),
    }
