"""Email notifications: approval requests and the daily report (Amazon SES)."""

from __future__ import annotations

from html import escape
from typing import Any, Protocol

from agent.approvals import Approval

Attachment = tuple[str, bytes, str]  # filename, content, MIME type


class Notifier(Protocol):
    def send(
        self, subject: str, html: str, text: str, attachments: list[Attachment] | None = None
    ) -> None: ...


class SesNotifier:
    def __init__(self, *, sender: str, recipient: str, region: str) -> None:
        import boto3

        self.client = boto3.client("sesv2", region_name=region)
        self.sender = sender
        self.recipient = recipient

    def send(
        self, subject: str, html: str, text: str, attachments: list[Attachment] | None = None
    ) -> None:
        if attachments:
            raw = _mime(self.sender, self.recipient, subject, html, text, attachments)
            self.client.send_email(
                FromEmailAddress=self.sender,
                Destination={"ToAddresses": [self.recipient]},
                Content={"Raw": {"Data": raw}},
            )
            return
        self.client.send_email(
            FromEmailAddress=self.sender,
            Destination={"ToAddresses": [self.recipient]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": {"Html": {"Data": html}, "Text": {"Data": text}},
                }
            },
        )


class RecordingNotifier:
    """Keeps sent messages in memory; used in tests and dry runs without SES."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(
        self, subject: str, html: str, text: str, attachments: list[Attachment] | None = None
    ) -> None:
        self.sent.append(
            {"subject": subject, "html": html, "text": text, "attachments": attachments or []}
        )


def _mime(
    sender: str, recipient: str, subject: str, html: str, text: str, attachments: list[Attachment]
) -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, recipient
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for filename, content, mime in attachments:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def approval_email(approval: Approval, link: str) -> tuple[str, str, str]:
    """Subject, HTML and plain text for an approval request."""
    subject = f"[Approval needed] {approval.title}"
    body = _preview_text(approval.preview)
    text = (
        f"{approval.title}\n\n{body}\n\n"
        f"Review, edit, approve or reject: {link}\n"
        f"This request expires at {approval.expires_at} (UTC)."
    )
    html = f"""<div style="font-family:system-ui,sans-serif;max-width:640px">
<h2 style="margin:0 0 12px">{escape(approval.title)}</h2>
<pre style="white-space:pre-wrap;font-family:inherit;background:#f6f6f6;padding:12px;border-radius:8px">{escape(body)}</pre>
<p><a href="{escape(link)}" style="display:inline-block;background:#0a66c2;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">Review and decide</a></p>
<p style="color:#666;font-size:12px">Nothing is published or marked ready until you decide. Expires {escape(approval.expires_at)} UTC.</p>
</div>"""
    return subject, html, text


def report_email(date_label: str, entries: list[dict[str, Any]]) -> tuple[str, str, str]:
    subject = f"Personal Agent daily report: {date_label}"
    if not entries:
        lines = ["No activity in the last 24 hours."]
    else:
        lines = [f"{e['at'][11:16]} UTC  [{e['kind']}]  {e['message']}" for e in entries]
    text = "\n".join(lines)
    items = "".join(f"<li>{escape(line)}</li>" for line in lines)
    html = f"""<div style="font-family:system-ui,sans-serif;max-width:640px">
<h2>Daily report: {escape(date_label)}</h2><ul>{items}</ul></div>"""
    return subject, html, text


def pack_email(job: dict[str, Any], checks: list[str]) -> tuple[str, str, str]:
    """The apply pack for one approved job; the PDFs travel as attachments."""
    subject = f"Apply pack: {job['title']} at {job['company']}"
    adjusted = [c for c in checks if c != "passed"]
    lines = [
        f"{job['title']} at {job['company']} ({job.get('location', '')})",
        f"Match {job.get('score', '?')}: {job.get('reason', '')}",
    ]
    if job.get("gaps"):
        lines.append("Gaps to address: " + "; ".join(job["gaps"]))
    lines.append(
        f"Fact check: {len(checks) - len(adjusted)} of {len(checks)} items passed as written"
        + (f"; {len(adjusted)} were replaced or removed." if adjusted else ".")
    )
    lines.append(f"Apply here (you submit): {job.get('url', '')}")
    text = (
        "\n".join(lines)
        + "\n\nYour tailored resume and cover letter are attached. Read them before you submit."
    )
    items = "".join(f"<p>{escape(line)}</p>" for line in lines[:-1])
    html = f"""<div style="font-family:system-ui,sans-serif;max-width:640px">
<h2 style="margin:0 0 12px">{escape(job["title"])} at {escape(job["company"])}</h2>{items}
<p><a href="{escape(job.get("url", ""))}" style="display:inline-block;background:#0a66c2;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">Open the job posting</a></p>
<p style="color:#666;font-size:12px">Your tailored resume and cover letter are attached. Read them before you submit; nothing has been sent to the employer.</p></div>"""
    return subject, html, text


def _preview_text(preview: dict[str, Any]) -> str:
    if "text" in preview:
        return str(preview["text"])
    if "jobs" in preview:
        return "\n\n".join(
            f"- {j['title']} at {j['company']} ({j.get('location', '')}), "
            f"match {j.get('score', '?')}\n  {j.get('reason') or ''}\n  {j['url']}"
            for j in preview["jobs"]
        )
    return str(preview)
