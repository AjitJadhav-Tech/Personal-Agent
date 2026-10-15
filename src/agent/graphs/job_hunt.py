"""Job search: discover jobs from allowed sources, score them, ask for approval, then prepare a
tailored apply pack (resume + cover letter) for each approved job and email it to the owner.

Submission always stays with the owner. Nothing is sent to employers.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agent.graphs.review import await_decision, request_approval
from agent.notify import pack_email
from agent.profile import load_master_profile, load_preferences
from agent.services import get_services
from agent.sources import ashby, greenhouse, lever

TASK = "job_hunt"
WORKERS = 4

# Public, read-only job board APIs that companies publish for this purpose.
SOURCES: dict[str, Callable[[str], list[dict[str, Any]]]] = {
    "greenhouse": greenhouse.fetch_board,
    "ashby": ashby.fetch_board,
    "lever": lever.fetch_board,
}


class JobState(TypedDict, total=False):
    shortlist: list[dict[str, Any]]
    approval_id: str
    decision: dict[str, Any]
    approved: list[dict[str, Any]]
    packs: list[dict[str, Any]]
    status: str


def prefilter_score(job: dict[str, Any], prefs: dict[str, Any]) -> int:
    """Cheap keyword score (0-100) that decides which jobs are worth a model call."""
    title = job["title"].lower()
    text = f"{title} {job['description'].lower()}"
    if any(word.lower() in title for word in prefs.get("exclude_title_words", [])):
        return 0
    locations = [loc.lower() for loc in prefs.get("locations", [])]
    if locations and not any(loc in job["location"].lower() for loc in locations):
        return 0
    title_hit = any(t.lower() in title for t in prefs.get("target_titles", []))
    skills = prefs.get("skills", [])
    hits = sum(1 for s in skills if re.search(rf"\b{re.escape(s.lower())}\b", text))
    # Full skill credit at 8 matches, so a long skills list doesn't dilute the score.
    skill_score = 60 * min(hits, 8) / min(len(skills), 8) if skills else 0
    return round((40 if title_hit else 0) + skill_score)


def discover(state: JobState) -> JobState:
    svc = get_services()
    prefs = load_preferences().get("jobs", {})
    profile = load_master_profile()

    fetched: dict[str, dict[str, Any]] = {}
    for source in prefs.get("sources", []):
        fetch = SOURCES.get(source["type"])
        if fetch is None:
            svc.store.log(TASK, f"Unknown source type: {source['type']}")
            continue
        for board in source.get("boards", []):
            try:
                for job in fetch(board):
                    fetched[job["id"]] = job
            except Exception as e:  # one broken board must not stop the run
                svc.store.log(TASK, f"Source failed: {source['type']}/{board}: {e}")

    names = prefs.get("company_names") or {}
    for job in fetched.values():
        job["company"] = names.get(job["company"], job["company"].replace("-", " ").title())

    seen = svc.store.seen_jobs(list(fetched))
    new = [j for j in fetched.values() if j["id"] not in seen]
    for job in new:
        job["prefilter"] = prefilter_score(job, prefs)
    candidates = sorted(
        (j for j in new if j["prefilter"] >= prefs.get("prefilter_min", 30)),
        key=lambda j: -j["prefilter"],
    )[: prefs.get("llm_score_limit", 20)]

    def score(job: dict[str, Any]) -> dict[str, Any] | None:
        from agent.tailoring import score_job

        try:
            return {**job, **score_job(svc.drafter, profile, job, prefs)}
        except Exception as e:
            svc.store.log(TASK, f"Scoring failed for {job['title']} at {job['company']}: {e}")
            return None

    with ThreadPoolExecutor(WORKERS) as pool:
        scored = list(pool.map(score, candidates))
    failed = {c["id"] for c, s in zip(candidates, scored, strict=True) if s is None}
    svc.store.mark_seen([j["id"] for j in new if j["id"] not in failed])  # retry failures later

    threshold = prefs.get("min_score", 65)
    picked = sorted((s for s in scored if s and s["score"] >= threshold), key=lambda j: -j["score"])
    picked = picked[: prefs.get("max_per_run", 10)]
    for job in picked:
        svc.store.put_job(job)
    svc.store.log(
        TASK,
        f"Fetched {len(fetched)} jobs, {len(new)} new, scored {len(candidates)}, "
        f"shortlisted {len(picked)}",
    )
    keep = ("id", "company", "title", "location", "url", "score", "reason", "gaps")
    return {"shortlist": [{k: j.get(k) for k in keep} for j in picked]}


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
    svc.store.log(TASK, f"Approved {len(approved)} job(s) for apply packs")
    return {"approved": approved, "status": "approved"}


def has_approved(state: JobState) -> Literal["build_packs", "__end__"]:
    return "build_packs" if state.get("approved") else END


def build_packs(state: JobState) -> JobState:
    svc = get_services()
    profile = load_master_profile()

    def build(meta: dict[str, Any]) -> dict[str, Any]:
        from agent.documents import cover_letter_pdf, resume_pdf
        from agent.tailoring import tailor_resume, write_cover_letter

        try:
            job = svc.store.get_job(meta["id"]) or meta
            resume = tailor_resume(svc.drafter, profile, job)
            letter = write_cover_letter(svc.drafter, profile, job)
            name = profile["basics"]["name"].replace(" ", "_")
            company = re.sub(r"\W+", "_", job["company"]).strip("_")
            attachments = [
                (f"{name}_Resume_{company}.pdf", resume_pdf(resume), "application/pdf"),
                (f"{name}_Cover_Letter_{company}.pdf", cover_letter_pdf(letter), "application/pdf"),
            ]
            checks = resume["checks"] + letter["checks"]
            svc.notifier.send(*pack_email(job, checks), attachments=attachments)
            svc.store.log(
                TASK,
                f"Apply pack emailed: {job['title']} at {job['company']}",
                {"url": job.get("url")},
            )
            return {"id": job["id"], "status": "sent", "checks": checks}
        except Exception as e:
            svc.store.log(TASK, f"Apply pack failed for {meta['title']} at {meta['company']}: {e}")
            return {"id": meta["id"], "status": "failed", "error": str(e)}

    with ThreadPoolExecutor(min(WORKERS, len(state["approved"]))) as pool:
        packs = list(pool.map(build, state["approved"]))
    return {"packs": packs, "status": "packs_sent"}


def build() -> StateGraph:
    g = StateGraph(JobState)
    g.add_node("discover", discover)
    g.add_node("ask_for_approval", ask_for_approval)
    g.add_node("await_decision", await_decision)
    g.add_node("record_decision", record_decision)
    g.add_node("build_packs", build_packs)
    g.add_edge(START, "discover")
    g.add_conditional_edges("discover", has_matches)
    g.add_edge("ask_for_approval", "await_decision")
    g.add_edge("await_decision", "record_decision")
    g.add_conditional_edges("record_decision", has_approved)
    g.add_edge("build_packs", END)
    return g
