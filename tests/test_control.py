"""Control page: signed link, status view, and the actions it can start."""

from urllib.parse import urlencode

from langgraph.checkpoint.memory import InMemorySaver

from agent.approvals import CONTROL_ID, control_link, sign
from agent.supervisor import compile_all, handle
from approval_api.handler import handler


def _event(method="GET", token=None, form=None):
    event = {
        "rawPath": "/control",
        "requestContext": {"http": {"method": method}, "domainName": "api.example.com"},
    }
    if method == "GET":
        event["queryStringParameters"] = {"t": token}
    else:
        event["body"] = urlencode(form or {})
    return event


def _token():
    return sign(CONTROL_ID, "test-secret")


def test_control_page_needs_the_signed_link(services):
    assert handler(_event(token="guess"))["statusCode"] == 403
    page = handler(_event(token=_token()))
    assert page["statusCode"] == 200
    assert "Ask for a post" in page["body"] and "Running on schedule" in page["body"]


def test_post_request_starts_a_content_run(services):
    started = []
    form = {"t": _token(), "action": "post", "topic": "Agents on AWS", "notes": "cut costs"}
    resp = handler(_event("POST", form=form), start_run=started.append)
    assert resp["statusCode"] == 200
    assert started == [
        {"task": "content", "input": {"topic": "Agents on AWS", "notes": "cut costs"}}
    ]
    assert (
        handler(_event("POST", form={**form, "topic": " "}), start_run=started.append)["statusCode"]
        == 400
    )


def test_run_now_and_pause(services):
    started = []
    for action in ("job_hunt", "daily_report"):
        handler(_event("POST", form={"t": _token(), "action": action}), start_run=started.append)
    assert [p["task"] for p in started] == ["job_hunt", "daily_report"]
    handler(_event("POST", form={"t": _token(), "action": "pause"}), start_run=started.append)
    assert services.paused is True
    page = handler(_event(token=_token()))["body"]
    assert "Paused" in page and 'value="resume"' in page


def test_requested_topic_reaches_the_draft(services):
    graphs = compile_all(InMemorySaver())
    handle(graphs, {"task": "content", "input": {"topic": "Agents on AWS", "notes": "n"}})
    assert "Agents on AWS" in services.notifier.sent[-1]["subject"]


def test_daily_report_carries_the_control_link(services):
    handle(compile_all(InMemorySaver()), {"task": "daily_report"})
    assert (
        control_link("https://approve.example.com", "test-secret")
        in services.notifier.sent[-1]["text"]
    )
