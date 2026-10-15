"""LinkedIn post workflow: draft, ask for approval, then publish only what was approved."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agent import linkedin
from agent.graphs.review import await_decision, request_approval
from agent.profile import load_preferences
from agent.services import get_services

TASK = "content"

SYSTEM = """You write LinkedIn posts for one professional, in their own voice.
Rules:
- Ground the post in the notes provided. Never invent experience, employers, numbers or results.
- Plain, specific language. No hashtag stuffing (at most 3), no emoji walls, no clichés such as
  "in today's fast-paced world" or "I'm thrilled to announce".
- 120 to 220 words, short paragraphs, end with a genuine question or takeaway.
Return only the post text."""


class ContentState(TypedDict, total=False):
    topic: str
    notes: str
    draft: str
    approval_id: str
    decision: dict[str, Any]
    final_text: str
    status: str


def draft_post(state: ContentState) -> ContentState:
    prefs = load_preferences().get("content", {})
    topic = state.get("topic") or prefs.get("default_topic", "a lesson from my recent work")
    notes = state.get("notes") or prefs.get("positioning", "")
    prompt = f"Topic: {topic}\n\nNotes from the author:\n{notes or '(none provided)'}"
    draft = get_services().drafter(SYSTEM, prompt)
    get_services().store.log(TASK, f"Drafted a post about: {topic}")
    return {"topic": topic, "draft": draft}


def ask_for_approval(state: ContentState, config: RunnableConfig) -> ContentState:
    return request_approval(
        state,
        config,
        kind="post",
        task=TASK,
        title=f"LinkedIn post: {state['topic']}",
        preview={"text": state["draft"]},
    )


def route_decision(state: ContentState) -> Literal["publish", "record_rejection"]:
    return "record_rejection" if state["decision"]["action"] == "reject" else "publish"


def publish(state: ContentState) -> ContentState:
    svc = get_services()
    decision = state["decision"]
    text = decision["text"] if decision["action"] == "edit" else state["draft"]
    if svc.is_paused():
        svc.store.log(TASK, "Approved post not published: agent is paused")
        return {"final_text": text, "status": "paused"}
    if svc.settings.dry_run:
        svc.store.log(TASK, "Dry run: approved post would be published now", {"chars": len(text)})
        return {"final_text": text, "status": "dry_run"}
    account = svc.linkedin
    if account is None or account.record() is None:
        svc.store.log(
            TASK,
            "Approved post not published: LinkedIn is not connected. Use the connect link in "
            "the daily report, then paste the approved text on LinkedIn yourself.",
        )
        return {"final_text": text, "status": "not_connected"}
    urn = account.publish(text)
    svc.store.log(TASK, f"Published on LinkedIn: {linkedin.post_url(urn)}", {"urn": urn})
    return {"final_text": text, "status": "published"}


def record_rejection(state: ContentState) -> ContentState:
    reason = state["decision"].get("reason") or "no reason given"
    get_services().store.log(TASK, f"Post rejected: {reason}")
    return {"status": "rejected"}


def build() -> StateGraph:
    g = StateGraph(ContentState)
    g.add_node("draft_post", draft_post)
    g.add_node("ask_for_approval", ask_for_approval)
    g.add_node("await_decision", await_decision)
    g.add_node("publish", publish)
    g.add_node("record_rejection", record_rejection)
    g.add_edge(START, "draft_post")
    g.add_edge("draft_post", "ask_for_approval")
    g.add_edge("ask_for_approval", "await_decision")
    g.add_conditional_edges("await_decision", route_decision)
    g.add_edge("publish", END)
    g.add_edge("record_rejection", END)
    return g
