"""Web app API and the opportunity workflow."""

import json

from langgraph.checkpoint.memory import InMemorySaver

from agent.supervisor import compile_all, handle
from approval_api.handler import handler

CLAIMS = {"jwt": {"claims": {"email": "owner@example.com"}}}


def _api(method, path, body=None, signed_in=True):
    ctx = {"http": {"method": method}, "domainName": "api.example.com"}
    if signed_in:
        ctx["authorizer"] = CLAIMS
    event = {"rawPath": f"/api{path}", "requestContext": ctx}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _call(event, started=None):
    resp = handler(event, start_run=(started if started is not None else []).append)
    return resp["statusCode"], json.loads(resp["body"])


def test_api_requires_sign_in(services):
    assert _call(_api("GET", "/status", signed_in=False))[0] == 401


def test_status_lists_pending_approvals(services):
    handle(compile_all(InMemorySaver()), {"task": "content", "thread_id": "p1"})
    code, body = _call(_api("GET", "/status"))
    assert code == 200
    assert body["paused"] is False and body["dry_run"] is True
    (pending,) = body["pending"]
    assert pending["url"].startswith("https://api.example.com/a/")


def test_post_and_run_requests_start_runs(services):
    started = []
    assert _call(_api("POST", "/posts", {"topic": "Agents", "notes": "n"}), started)[0] == 202
    assert _call(_api("POST", "/posts", {"topic": " "}), started)[0] == 400
    assert _call(_api("POST", "/run", {"task": "job_hunt"}), started)[0] == 202
    assert _call(_api("POST", "/run", {"task": "content"}), started)[0] == 400
    assert [p["task"] for p in started] == ["content", "job_hunt"]
    assert _call(_api("POST", "/pause", {"paused": True}))[1] == {"paused": True}
    assert services.paused is True


def test_opportunity_is_drafted_and_emailed(services):
    started = []
    text = "Hi Ajit, Priya from Acme AI here. Are you open to a GenAI Engineer role in Pune?"
    code, body = _call(_api("POST", "/opportunities", {"text": text}), started)
    assert code == 202 and body["opportunity"]["status"] == "drafting"

    (run,) = started
    handle(compile_all(InMemorySaver()), {**run, "thread_id": "opp-1"})
    (opp,) = _call(_api("GET", "/opportunities"))[1]["opportunities"]
    assert opp["status"] == "ready"
    assert opp["reply"]["subject"] == "Re: GenAI Engineer at Acme AI"
    assert "AgentCore" in opp["reply"]["body"]
    assert "Nobel" not in opp["reply"]["body"]  # unsupported claim removed by the fact check
    mail = services.notifier.sent[-1]
    assert "Reply drafted: GenAI Engineer at Acme AI" == mail["subject"]
    assert "mailto:priya%40acme.example" in mail["html"]


def test_web_app_page_carries_its_config(services, monkeypatch):
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client-1")
    resp = handler(
        {
            "rawPath": "/app",
            "requestContext": {"http": {"method": "GET"}, "domainName": "api.example.com"},
        }
    )
    assert resp["statusCode"] == 200
    assert '"clientId": "client-1"' in resp["body"]
    assert '"redirectUri": "https://api.example.com/app"' in resp["body"]
