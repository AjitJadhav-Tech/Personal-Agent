"""LinkedIn connection and publishing, with LinkedIn's HTTP endpoints faked."""

import dataclasses
from urllib.parse import parse_qs, urlparse

from langgraph.checkpoint.memory import InMemorySaver
from test_approval_flow import _decide

from agent import linkedin
from agent.supervisor import compile_all, handle
from approval_api.handler import handler


class FakeLinkedIn:
    def __init__(self, record=None):
        self._record = record
        self.published = []

    def record(self):
        return self._record

    def save(self, record):
        self._record = record

    def client_secret(self):
        return "client-secret"

    def publish(self, text):
        self.published.append(text)
        return "urn:li:share:123"


CONNECTED = {
    "access_token": "tok",
    "expires_at": "2099-01-01T00:00:00+00:00",
    "person_urn": "urn:li:person:abc",
    "name": "Ajit Jadhav",
}


def _configure(services, record=None, dry_run=True):
    services.settings = dataclasses.replace(
        services.settings, linkedin_client_id="cid", dry_run=dry_run
    )
    services.linkedin = FakeLinkedIn(record)
    return services.linkedin


def _oauth_event(step, **params):
    return {
        "rawPath": f"/oauth/linkedin/{step}",
        "requestContext": {"http": {"method": "GET"}, "domainName": "api.example.com"},
        "queryStringParameters": params,
    }


def test_little_text_escapes_reserved_characters_and_keeps_hashtags():
    out = linkedin.to_little_text("Cut costs (by 40%) with [agents] #GenAI #AWS_Bedrock")
    assert (
        out == r"Cut costs \(by 40%\) with \[agents\] {hashtag|\#|GenAI} {hashtag|\#|AWS_Bedrock}"
    )


def test_oauth_state_expires():
    state = linkedin.make_state("s", now=1000)
    assert linkedin.check_state(state, "s", now=1100)
    assert not linkedin.check_state(state, "s", now=1000 + 601)
    assert not linkedin.check_state(state, "other", now=1100)


def test_connect_requires_the_signed_link(services):
    _configure(services)
    assert handler(_oauth_event("start", t="guess"))["statusCode"] == 403
    resp = handler(_oauth_event("start", t=linkedin.connect_token("test-secret")))
    assert resp["statusCode"] == 302
    query = parse_qs(urlparse(resp["headers"]["location"]).query)
    assert query["client_id"] == ["cid"]
    assert query["redirect_uri"] == ["https://api.example.com/oauth/linkedin/callback"]
    assert query["scope"] == ["openid profile w_member_social"]


def test_callback_stores_the_token(services, monkeypatch):
    account = _configure(services)
    calls = {}

    def exchange(code, client_id, client_secret, redirect_uri):
        calls.update(code=code, secret=client_secret, redirect=redirect_uri)
        return {"access_token": "tok", "expires_in": 5184000}

    monkeypatch.setattr(linkedin, "exchange_code", exchange)
    monkeypatch.setattr(linkedin, "userinfo", lambda _: {"sub": "abc", "name": "Ajit Jadhav"})
    state = linkedin.make_state("test-secret")
    resp = handler(_oauth_event("callback", code="c1", state=state))
    assert resp["statusCode"] == 200, resp["body"]
    assert calls == {
        "code": "c1",
        "secret": "client-secret",
        "redirect": "https://api.example.com/oauth/linkedin/callback",
    }
    assert account.record()["person_urn"] == "urn:li:person:abc"
    assert handler(_oauth_event("callback", code="c1", state="1.bad"))["statusCode"] == 403


def test_approved_post_is_published_when_live(services):
    account = _configure(services, CONNECTED, dry_run=False)
    graphs = compile_all(InMemorySaver())
    handle(graphs, {"task": "content", "thread_id": "post-live"})
    out = handle(graphs, _decide(services, {"action": "approve"}))
    assert out["status"] == "published"
    assert len(account.published) == 1
    assert any("feed/update/urn:li:share:123" in e["message"] for e in services.store.activity)


def test_approved_post_waits_when_linkedin_not_connected(services):
    account = _configure(services, None, dry_run=False)
    graphs = compile_all(InMemorySaver())
    handle(graphs, {"task": "content", "thread_id": "post-offline"})
    out = handle(graphs, _decide(services, {"action": "approve"}))
    assert out["status"] == "not_connected"
    assert account.published == []


def test_daily_report_offers_the_connect_link(services):
    _configure(services, None)
    handle(compile_all(InMemorySaver()), {"task": "daily_report"})
    text = services.notifier.sent[-1]["text"]
    assert "LinkedIn is not connected" in text
    assert "https://approve.example.com/oauth/linkedin/start?t=" in text


def test_publish_steps_back_from_a_retired_api_version(monkeypatch):
    versions = []

    def fake_request(url, *, data=None, headers=None):
        versions.append(headers["LinkedIn-Version"])
        if headers["LinkedIn-Version"] == "202606":
            return 426, {}, b"NONEXISTENT_VERSION"
        return 201, {"x-restli-id": "urn:li:share:9"}, b""

    monkeypatch.setattr(linkedin, "_request", fake_request)
    assert linkedin.publish_post("tok", "urn:li:person:abc", "hi", "202606") == "urn:li:share:9"
    assert versions == ["202606", "202605"]
