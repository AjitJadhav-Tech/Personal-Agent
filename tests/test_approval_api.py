from urllib.parse import urlencode

from agent.approvals import Approval, normalize_decision, sign, verify
from approval_api.handler import handler


def _post(approval_id, form):
    return {
        "requestContext": {"http": {"method": "POST"}},
        "pathParameters": {"id": approval_id},
        "body": urlencode(form),
    }


def _approval(services, **kw):
    a = Approval.new(
        kind="post", task="content", thread_id="t1", title="Post", preview={"text": "hello"}, **kw
    )
    services.store.put_approval(a)
    return a


def test_token_is_bound_to_the_approval():
    assert verify("a1", sign("a1", "s"), "s")
    assert not verify("a2", sign("a1", "s"), "s")
    assert not verify("a1", sign("a1", "other"), "s")


def test_bad_token_is_rejected(services):
    a = _approval(services)
    resp = handler(_post(a.approval_id, {"t": "nope", "action": "reject"}), resume=lambda _: None)
    assert resp["statusCode"] == 403
    assert a.status == "pending"


def test_second_decision_is_refused(services):
    a = _approval(services)
    form = {"t": sign(a.approval_id, "test-secret"), "action": "reject"}
    resumed = []
    assert handler(_post(a.approval_id, form), resume=resumed.append)["statusCode"] == 200
    assert handler(_post(a.approval_id, form), resume=resumed.append)["statusCode"] == 409
    assert len(resumed) == 1


def test_expired_request_cannot_be_approved(services):
    a = _approval(services)
    a.expires_at = "2000-01-01T00:00:00+00:00"
    form = {"t": sign(a.approval_id, "test-secret"), "action": "edit", "text": "x"}
    assert handler(_post(a.approval_id, form), resume=lambda _: None)["statusCode"] == 410


def test_edit_requires_text():
    try:
        normalize_decision({"action": "edit", "text": "  "})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
