"""Control page: one bookmarked, signed link to steer the agent without the AWS console.

GET  /control?t=<token>  shows status and recent activity. It changes nothing (mail scanners
                         open links).
POST /control            runs one action: request a post, search jobs, send the report, pause or
                         resume.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

from agent import linkedin
from agent.approvals import CONTROL_ID, verify

TASKS = {"job_hunt": "Job search started", "daily_report": "Daily report on its way"}


def handle(event: dict[str, Any], svc, form: dict[str, Any] | None, start_run, page) -> dict:
    base_url = f"https://{event.get('requestContext', {}).get('domainName', '')}"
    token = (form or event.get("queryStringParameters") or {}).get("t", "")
    if not verify(CONTROL_ID, token, svc.approval_secret):
        return page(403, "Link not valid", "<p>This link is not valid.</p>")

    notice = ""
    if form is not None:
        action = form.get("action", "")
        if action == "post":
            topic = (form.get("topic") or "").strip()
            if not topic:
                return page(
                    400, "Add a topic", "<p>Write a topic for the post, then try again.</p>"
                )
            notes = (form.get("notes") or "").strip()
            start_run({"task": "content", "input": {"topic": topic, "notes": notes}})
            svc.store.log("control", f"You asked for a post about: {topic}")
            notice = "Drafting your post. The approval email arrives in a few minutes."
        elif action in TASKS:
            start_run({"task": action})
            svc.store.log("control", f"You started: {action}")
            notice = f"{TASKS[action]}. Results come by email."
        elif action in ("pause", "resume"):
            svc.set_paused(action == "pause")
            svc.store.log(
                "control", f"You {'paused' if action == 'pause' else 'resumed'} the agent"
            )
            notice = "Agent paused." if action == "pause" else "Agent resumed."
        else:
            return page(400, "Unknown action", "<p>Unknown action.</p>")

    return page(200, "Personal Agent", _body(svc, token, notice, base_url))


def _body(svc, token: str, notice: str, base_url: str) -> str:
    now = datetime.now(UTC)
    paused = svc.is_paused()
    status = [
        "Paused: nothing goes out until you resume." if paused else "Running on schedule.",
        "Dry run: approved posts are not published."
        if svc.settings.dry_run
        else "Live: approved posts are published.",
        _linkedin_line(svc, now, base_url),
    ]
    entries = sorted(svc.store.activity_since(now - timedelta(days=3)), key=lambda e: e["at"])
    recent = (
        "".join(
            f"<li><small>{escape(e['at'][5:16].replace('T', ' '))} UTC</small> {escape(e['message'])}</li>"
            for e in reversed(entries[-15:])
        )
        or "<li>No activity in the last 3 days.</li>"
    )
    hidden = f'<input type="hidden" name="t" value="{escape(token)}">'
    toggle = "resume" if paused else "pause"
    return f"""{f'<p class="note">{escape(notice)}</p>' if notice else ""}
<ul>{"".join(f"<li>{line}</li>" for line in status)}</ul>
<h3>Ask for a post</h3>
<form method="post">{hidden}
<input name="topic" placeholder="Topic, e.g. what I learned shipping an agent on AgentCore" style="width:100%">
<textarea name="notes" rows="4" placeholder="Notes: facts, numbers, a story. The agent sticks to these."></textarea>
<div class="row"><button name="action" value="post" class="primary">Draft it for my approval</button></div>
</form>
<h3>Run now</h3>
<form method="post">{hidden}<div class="row">
<button name="action" value="job_hunt">Search jobs</button>
<button name="action" value="daily_report">Send daily report</button>
<button name="action" value="{toggle}">{toggle.capitalize()} agent</button>
</div></form>
<h3>Recent activity</h3><ul>{recent}</ul>
<p><small>Bookmark this page and keep the link private: it controls your agent.</small></p>"""


def _linkedin_line(svc, now: datetime, base_url: str) -> str:
    if svc.linkedin is None or not svc.settings.linkedin_client_id:
        return "LinkedIn: not set up."
    link = linkedin.connect_link(base_url, svc.approval_secret)
    try:
        left = linkedin.days_left(svc.linkedin.record(), now)
    except Exception:
        left = None
    if left is not None and left > 7:
        return f"LinkedIn connected ({int(left)} days left)."
    state = "not connected" if left is None or left <= 0 else f"expires in {int(left)} days"
    return f'LinkedIn {state}. <a href="{escape(link)}">Connect LinkedIn</a>'
