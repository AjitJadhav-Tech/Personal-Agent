"""End-to-end approval flow: graph pauses, the approval page records a decision, the run resumes."""

from urllib.parse import urlencode

from langgraph.checkpoint.memory import InMemorySaver

from agent.approvals import sign
from agent.graphs import job_hunt
from agent.supervisor import compile_all, handle
from approval_api.handler import handler as approval_handler


def _graphs():
    return compile_all(InMemorySaver())


def _approval_event(approval_id, method="GET", token=None, form=None):
    event = {
        "requestContext": {"http": {"method": method}},
        "pathParameters": {"id": approval_id},
    }
    if method == "GET":
        event["queryStringParameters"] = {"t": token}
    else:
        event["body"] = urlencode(form or {}, doseq=True)
    return event


def _decide(svc, form):
    """Submit the approval page and return the payload the runner would receive."""
    (approval,) = [a for a in svc.store.approvals.values() if a.status == "pending"]
    resumed = []
    resp = approval_handler(
        _approval_event(
            approval.approval_id,
            "POST",
            form={"t": sign(approval.approval_id, svc.approval_secret), **form},
        ),
        resume=resumed.append,
    )
    assert resp["statusCode"] == 200, resp["body"]
    (a,) = resumed
    return {"task": a.task, "thread_id": a.thread_id, "decision": a.decision}


def test_post_waits_for_approval_then_publishes(services):
    graphs = _graphs()
    out = handle(graphs, {"task": "content", "thread_id": "post-1"})
    assert out["waiting_for_approval"] is True
    assert len(services.notifier.sent) == 1
    assert "Approval needed" in services.notifier.sent[0]["subject"]
    assert "https://approve.example.com/a/" in services.notifier.sent[0]["text"]

    # Opening the link (as a mail scanner would) changes nothing.
    (approval,) = services.store.approvals.values()
    page = approval_handler(
        _approval_event(approval.approval_id, token=sign(approval.approval_id, "test-secret"))
    )
    assert page["statusCode"] == 200 and approval.status == "pending"

    # Submitting the unchanged text counts as a plain approval.
    payload = _decide(services, {"action": "edit", "text": "Draft post about approvals."})
    assert payload["decision"] == {"action": "approve"}
    out = handle(graphs, payload)
    assert out == {
        "task": "content",
        "thread_id": "post-1",
        "waiting_for_approval": False,
        "status": "dry_run",
    }
    assert len(services.notifier.sent) == 1, "resuming must not resend the approval email"


def test_edited_post_uses_owner_text(services):
    graphs = _graphs()
    handle(graphs, {"task": "content", "thread_id": "post-2"})
    payload = _decide(services, {"action": "edit", "text": "My own words."})
    handle(graphs, payload)
    state = graphs["content"].get_state({"configurable": {"thread_id": "post-2"}}).values
    assert state["final_text"] == "My own words."


def test_rejected_post_is_not_published(services):
    graphs = _graphs()
    handle(graphs, {"task": "content", "thread_id": "post-3"})
    out = handle(graphs, _decide(services, {"action": "reject", "reason": "too generic"}))
    assert out["status"] == "rejected"
    assert any("too generic" in a["message"] for a in services.store.activity)


def test_paused_agent_does_not_publish(services):
    services.paused = True
    graphs = _graphs()
    handle(graphs, {"task": "content", "thread_id": "post-4"})
    out = handle(graphs, _decide(services, {"action": "edit", "text": "Approved text"}))
    assert out["status"] == "paused"


def test_job_batch_only_keeps_selected_jobs(services, monkeypatch):
    jobs = [
        {
            "id": "j1",
            "company": "acme",
            "title": "AI Engineer",
            "location": "Remote",
            "url": "https://x/1",
            "description": "python langgraph aws",
        },
        {
            "id": "j2",
            "company": "acme",
            "title": "Senior AI Engineer",
            "location": "Remote, US",
            "url": "https://x/2",
            "description": "python aws",
        },
        {
            "id": "j3",
            "company": "acme",
            "title": "AI Engineer Intern",
            "location": "Remote",
            "url": "https://x/3",
            "description": "python langgraph aws",
        },
        {
            "id": "j4",
            "company": "acme",
            "title": "Accountant",
            "location": "Remote",
            "url": "https://x/4",
            "description": "excel",
        },
    ]
    monkeypatch.setitem(job_hunt.SOURCES, "fake", lambda board: jobs)
    graphs = _graphs()
    out = handle(graphs, {"task": "job_hunt", "thread_id": "jobs-1"})
    assert out["waiting_for_approval"] is True
    (approval,) = services.store.approvals.values()
    assert [j["id"] for j in approval.preview["jobs"]] == ["j1", "j2"]

    out = handle(graphs, _decide(services, {"action": "approve", "selected": ["j2"]}))
    state = graphs["job_hunt"].get_state({"configurable": {"thread_id": "jobs-1"}}).values
    assert [j["id"] for j in state["approved"]] == ["j2"]
    assert out["status"] == "approved"


def test_no_matches_means_no_email(services, monkeypatch):
    monkeypatch.setitem(job_hunt.SOURCES, "fake", lambda board: [])
    out = handle(_graphs(), {"task": "job_hunt", "thread_id": "jobs-2"})
    assert out["waiting_for_approval"] is False
    assert services.notifier.sent == []


def test_daily_report_lists_activity(services):
    services.store.log("job_hunt", "Fetched 12 jobs")
    out = handle(_graphs(), {"task": "daily_report"})
    assert out["status"] == "sent"
    (mail,) = services.notifier.sent
    assert "Fetched 12 jobs" in mail["text"]
