"""Job discovery: fetch from allowed sources, score, and ask for approval before anything is
prepared for submission. Submission itself always stays with the owner."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agent.graphs.review import await_decision, request_approval
from agent.profile import load_preferences
from agent.services import get_services
from agent.sources import greenhouse

TASK = "job_hunt"

# Source adapters by name; phase 1 adds Lever, Ashby, Adzuna and job-alert emails.
SOURCES: dict[str, Callable[[str], list[dict[str, Any]]]] = {"greenhouse": greenhouse.fetch_board}


class JobState(TypedDict, total=False):
    jobs: list[dict[str, Any]]
    shortlist: list[dict[str, Any]]
    approval_id: str
    decision: dict[str, Any]
    approved: list[dict[str, Any]]
    status: str


def fetch_jobs(state: JobState) -> JobState:
    prefs = load_preferences().get("jobs", {})
    svc = get_services()
    jobs: list[dict[str, Any]] = []
    for source in prefs.get("sources", []):
        fetch = SOURCES.get(source["type"])
        if fetch is None:
            continue
        for board in source.get("boards", []):
            try:
                jobs.extend(fetch(board))
            except Exception as e:  # one broken board must not stop the run
                svc.store.log(TASK, f"Source failed: {source['type']}/{board}: {e}")
    svc.store.log(TASK, f"Fetched {len(jobs)} jobs")
    return {"jobs": jobs}


def score(job: dict[str, Any], prefs: dict[str, Any]) -> int:
    """Phase 0 keyword score (0-100). Phase 1 replaces it with embeddings + an LLM rubric."""
    title = job["title"].lower()
    text = f"{title} {job['description'].lower()}"
    if any(word.lower() in title for word in prefs.get("exclude_title_words", [])):
        return 0
    locations = [loc.lower() for loc in prefs.get("locations", [])]
    if locations and not any(loc in job["location"].lower() for loc in locations):
        return 0
    titles = prefs.get("target_titles", [])
    title_hit = any(t.lower() in title for t in titles)
    skills = prefs.get("skills", [])
    skill_hits = sum(1 for s in skills if re.search(rf"\b{re.escape(s.lower())}\b", text))
    skill_score = 60 * skill_hits / len(skills) if skills else 0
    return round((40 if title_hit else 0) + skill_score)


def shortlist(state: JobState) -> JobState:
    prefs = load_preferences().get("jobs", {})
    threshold = prefs.get("min_score", 60)
    cap = prefs.get("max_per_run", 10)
    scored = [{**j, "score": score(j, prefs)} for j in state.get("jobs", [])]
    picked = sorted((j for j in scored if j["score"] >= threshold), key=lambda j: -j["score"])[:cap]
    get_services().store.log(TASK, f"Shortlisted {len(picked)} of {len(scored)} jobs")
    return {"shortlist": [{k: v for k, v in j.items() if k != "description"} for j in picked]}


def has_matches(state: JobState) -> Literal["ask_for_approval", "__end__"]:
    return "ask_for_approval" if state.get("shortlist") else END


def ask_for_approval(state: JobState, config: RunnableConfig) -> JobState:
    n = len(state["shortlist"])
    return request_approval(
        state,
        config,
        kind="job_batch",
        task=TASK,
        title=f"{n} new job match{'es' if n != 1 else ''}",
        preview={"jobs": state["shortlist"]},
    )


def record_decision(state: JobState) -> JobState:
    decision = state["decision"]
    svc = get_services()
    if decision["action"] == "reject":
        svc.store.log(TASK, f"Job batch skipped: {decision.get('reason') or 'no reason given'}")
        return {"approved": [], "status": "rejected"}
    selected = decision.get("selected")
    approved = [j for j in state["shortlist"] if selected is None or j["id"] in selected]
    for job in approved:
        # Phase 1 tailors the resume and cover letter here and attaches them to the pack.
        svc.store.log(
            TASK,
            f"Approved for an apply pack: {job['title']} at {job['company']}",
            {"url": job["url"]},
        )
    return {"approved": approved, "status": "approved"}


def build() -> StateGraph:
    g = StateGraph(JobState)
    g.add_node("fetch_jobs", fetch_jobs)
    g.add_node("shortlist", shortlist)
    g.add_node("ask_for_approval", ask_for_approval)
    g.add_node("await_decision", await_decision)
    g.add_node("record_decision", record_decision)
    g.add_edge(START, "fetch_jobs")
    g.add_edge("fetch_jobs", "shortlist")
    g.add_conditional_edges("shortlist", has_matches)
    g.add_edge("ask_for_approval", "await_decision")
    g.add_edge("await_decision", "record_decision")
    g.add_edge("record_decision", END)
    return g
