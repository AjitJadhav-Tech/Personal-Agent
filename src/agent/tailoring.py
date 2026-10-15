"""Job matching and tailored application documents, grounded in the master profile.

The model never writes facts on its own authority. Every resume bullet and cover-letter
paragraph must cite fact ids from profile/master_profile.yaml, and three checks run before
anything reaches the owner:

1. Every cited id must exist (and a bullet must cite at least one).
2. Every number in the text must appear in the cited facts (or the job description, for
   statements about the role), so metrics can't be inflated or invented.
3. A separate model call reviews each item against its cited facts and flags anything they
   don't support.

Items that fail are revised once with the reviewer's notes; anything still failing falls back
to the cited fact's own wording. Skills are limited to the profile's skill list.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent.llm import Drafter, parse_json
from agent.profile import all_skills, fact_index, facts_by_experience, plain_facts

JD_LIMIT = 12000
NUMBER = re.compile(r"\d+(?:[.,]\d+)*\s*(?:%|\+|ms\b|x\b)?", re.IGNORECASE)

SCORE_SYSTEM = """Task: score_job
You assess how well a candidate fits a job. Use only the candidate facts given.
Return JSON only: {"score": 0-100, "must_haves_met": [..], "gaps": [..], "reason": "one sentence"}.
Scoring: 85+ strong fit on must-haves and seniority; 65-84 good fit with minor gaps;
below 65 missing must-haves, wrong seniority, or a location the candidate excluded."""

RESUME_SYSTEM = """Task: tailor_resume
You tailor a one-page resume to a job, using ONLY the candidate facts provided.
Rules:
- Every bullet cites the fact ids it is based on in "fact_ids". Rephrase and reorder to mirror the
  job's wording where truthful; never add tools, results, numbers, titles or scope the facts lack.
- Keep numbers exactly as in the facts. 3 to 5 bullets for the current role, 1 to 3 for others.
- "skills": up to 16 items chosen from the candidate skill list, most relevant first.
- "summary": 2 sentences, citing its fact ids in "summary_fact_ids".
Return JSON only:
{"summary": str, "summary_fact_ids": [ids], "skills": [str],
 "experience": [{"exp_id": str, "bullets": [{"text": str, "fact_ids": [ids]}]}]}"""

COVER_SYSTEM = """Task: cover_letter
You write a short cover letter (3 or 4 paragraphs, under 280 words) for the candidate.
Rules:
- Claims about the candidate cite fact ids in "fact_ids"; use ONLY those facts.
- Statements about the company or role must come from the job description; cite them as "jd".
- Specific and plain. No clichés ("I am thrilled", "fast-paced"), no flattery, no invented details.
Return JSON only: {"paragraphs": [{"text": str, "fact_ids": [ids]}]}"""

CHECK_SYSTEM = """Task: fact_check
You are a strict reviewer. For each numbered item, decide whether its cited facts fully support
every claim in it (tools, scope, results, numbers, seniority). Wording may differ; meaning may not.
Return JSON only: {"unsupported": [{"index": int, "problem": "what is not supported"}]}"""


def profile_brief(profile: dict[str, Any]) -> str:
    facts = fact_index(profile)
    lines = [f"[{fid}] {text}" for fid, text in facts.items()]
    return (
        "Candidate facts:\n"
        + "\n".join(lines)
        + "\n\nCandidate skills: "
        + ", ".join(all_skills(profile))
    )


def score_job(
    drafter: Drafter, profile: dict[str, Any], job: dict[str, Any], prefs: dict[str, Any]
) -> dict[str, Any]:
    prompt = (
        f"{profile_brief(profile)}\n\nCandidate locations: {', '.join(prefs.get('locations', []))}"
        f"\n\nJob: {job['title']} at {job['company']} ({job['location']})\n"
        f"{job['description'][:JD_LIMIT]}"
    )
    result = parse_json(drafter(SCORE_SYSTEM, prompt))
    return {
        "score": max(0, min(100, int(result.get("score", 0)))),
        "must_haves_met": [str(x) for x in result.get("must_haves_met", [])][:6],
        "gaps": [str(x) for x in result.get("gaps", [])][:6],
        "reason": str(result.get("reason", ""))[:300],
    }


def tailor_resume(drafter: Drafter, profile: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    facts = fact_index(profile)
    owned = facts_by_experience(profile)
    raw = parse_json(drafter(RESUME_SYSTEM, _job_prompt(profile, job)))

    items: list[dict[str, Any]] = []
    for exp in raw.get("experience", []):
        if exp.get("exp_id") in owned:
            for b in exp.get("bullets", []):
                items.append(
                    {
                        "exp_id": exp["exp_id"],
                        "text": str(b.get("text", "")),
                        "fact_ids": list(b.get("fact_ids", [])),
                    }
                )
    summary = {
        "exp_id": None,
        "text": str(raw.get("summary", "")),
        "fact_ids": list(raw.get("summary_fact_ids", [])),
    }
    plain = plain_facts(profile)
    checked = verify_items(drafter, facts, [summary, *items], owned=owned, jd=None, fallback=plain)

    allowed = {s.lower(): s for s in all_skills(profile)}
    skills = [allowed[s.lower()] for s in raw.get("skills", []) if s.lower() in allowed]
    skills = list(dict.fromkeys(skills))[:16] or all_skills(profile)[:16]

    experience = []
    for exp in profile.get("experience", []):
        bullets = [i["text"] for i in checked[1:] if i["exp_id"] == exp["id"]]
        if not bullets:  # keep every role on the page, using its own facts
            bullets = [plain[fid] for fid in owned[exp["id"]][:2]]
        experience.append(
            {
                "company": exp["company"],
                "title": exp["title"],
                "dates": f"{_month(exp['start'])} – {_month(exp['end'])}",
                "projects": [
                    f"{p['name'].split(' (')[0]} for {p['client']}"
                    if p.get("client")
                    else p["name"]
                    for p in exp.get("projects", [])
                ],
                "bullets": bullets,
            }
        )
    return {
        "basics": profile.get("basics", {}),
        "summary": checked[0]["text"],
        "skills": skills,
        "experience": experience,
        "certifications": [c["name"] for c in profile.get("certifications", [])],
        "education": [
            f"{e['degree']}, {e['institution']} ({e['year']})" for e in profile.get("education", [])
        ],
        "checks": [i["check"] for i in checked],
    }


def write_cover_letter(
    drafter: Drafter, profile: dict[str, Any], job: dict[str, Any]
) -> dict[str, Any]:
    facts = fact_index(profile)
    raw = parse_json(drafter(COVER_SYSTEM, _job_prompt(profile, job)))
    items = [
        {"exp_id": None, "text": str(p.get("text", "")), "fact_ids": list(p.get("fact_ids", []))}
        for p in raw.get("paragraphs", [])
    ]
    checked = verify_items(
        drafter, facts, items, owned=None, jd=job["description"], drop_failures=True
    )
    return {
        "basics": profile.get("basics", {}),
        "company": job["company"],
        "title": job["title"],
        "paragraphs": [i["text"] for i in checked if i["text"]],
        "checks": [i["check"] for i in checked],
    }


def verify_items(
    drafter: Drafter,
    facts: dict[str, str],
    items: list[dict[str, Any]],
    *,
    owned: dict[str, list[str]] | None,
    jd: str | None,
    drop_failures: bool = False,
    fallback: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Runs the three checks; revises failures once, then falls back or drops them."""
    for item in items:
        item["problems"] = mechanical_problems(item, facts, owned, jd)
    _review(drafter, facts, items, jd)

    failing = [i for i in items if i["problems"]]
    if failing:
        _revise(drafter, facts, failing, jd)
        for item in failing:
            item["problems"] = mechanical_problems(item, facts, owned, jd)
        _review(drafter, facts, failing, jd)

    for item in items:
        if not item["problems"]:
            item["check"] = "passed"
            continue
        valid = [f for f in item["fact_ids"] if f in facts]
        if drop_failures or not valid:
            item["text"], item["check"] = "", f"removed: {item['problems'][0]}"
        else:
            item["text"] = (fallback or facts)[valid[0]]
            item["check"] = f"replaced with fact wording: {item['problems'][0]}"
    return items


def mechanical_problems(
    item: dict[str, Any], facts: dict[str, str], owned: dict[str, list[str]] | None, jd: str | None
) -> list[str]:
    problems = []
    ids = item["fact_ids"]
    cited = [f for f in ids if f in facts]
    if not item["text"].strip():
        problems.append("empty")
    if not cited and not (jd and "jd" in ids):
        problems.append("cites no profile fact")
    unknown = [f for f in ids if f not in facts and f != "jd"]
    if unknown:
        problems.append(f"unknown fact ids {unknown}")
    if owned and item.get("exp_id") and any(f not in owned[item["exp_id"]] for f in cited):
        problems.append("cites a fact from a different role")
    source = " ".join(facts[f] for f in cited) + (" " + jd if jd and "jd" in ids else "")
    source_numbers = {_norm(n) for n in NUMBER.findall(source)}
    for n in NUMBER.findall(item["text"]):
        if _norm(n) not in source_numbers:
            problems.append(f"number '{n.strip()}' is not in the cited facts")
    return problems


def _review(
    drafter: Drafter, facts: dict[str, str], items: list[dict[str, Any]], jd: str | None
) -> None:
    candidates = [(n, i) for n, i in enumerate(items) if not i["problems"] and i["text"].strip()]
    if not candidates:
        return
    blocks = []
    for n, item in candidates:
        cited = "\n".join(f"  [{f}] {facts[f]}" for f in item["fact_ids"] if f in facts)
        if jd and "jd" in item["fact_ids"]:
            cited += "\n  [jd] (statements about the company or role, from the job description)"
        blocks.append(f"Item {n}: {item['text']}\nCited facts:\n{cited}")
    prompt = "\n\n".join(blocks)
    if jd:
        prompt += f"\n\nJob description (for [jd] citations):\n{jd[:JD_LIMIT]}"
    try:
        verdict = parse_json(drafter(CHECK_SYSTEM, prompt))
    except ValueError:
        for _, item in candidates:  # an unreadable review fails closed
            item["problems"] = ["fact check could not be read"]
        return
    for entry in verdict.get("unsupported", []):
        idx = entry.get("index")
        if isinstance(idx, int) and 0 <= idx < len(items):
            items[idx]["problems"].append(str(entry.get("problem", "unsupported claim")))


def _revise(
    drafter: Drafter, facts: dict[str, str], items: list[dict[str, Any]], jd: str | None
) -> None:
    payload = [
        {"index": n, "text": i["text"], "fact_ids": i["fact_ids"], "problems": i["problems"]}
        for n, i in enumerate(items)
    ]
    system = """Task: revise_items
Rewrite each item so its cited facts fully support it, fixing the listed problems. Use only these
facts; keep numbers exactly as in them. Return JSON only:
{"items": [{"index": int, "text": str, "fact_ids": [ids]}]}"""
    prompt = "Facts:\n" + "\n".join(f"[{k}] {v}" for k, v in facts.items())
    prompt += "\n\nItems:\n" + json.dumps(payload, ensure_ascii=False)
    try:
        revised = parse_json(drafter(system, prompt)).get("items", [])
    except ValueError:
        return
    for entry in revised:
        idx = entry.get("index")
        if isinstance(idx, int) and 0 <= idx < len(items):
            items[idx]["text"] = str(entry.get("text", ""))
            items[idx]["fact_ids"] = list(entry.get("fact_ids", items[idx]["fact_ids"]))


def _job_prompt(profile: dict[str, Any], job: dict[str, Any]) -> str:
    roles = "\n".join(
        f"- exp_id {e['id']}: {e['title']} at {e['company']} ({e['start']} to {e['end']}); "
        f"fact ids: {', '.join(ids)}"
        for e, ids in ((e, facts_by_experience(profile)[e["id"]]) for e in profile["experience"])
    )
    return (
        f"{profile_brief(profile)}\n\nRoles:\n{roles}\n\n"
        f"Job: {job['title']} at {job['company']} ({job['location']})\n"
        f"{job['description'][:JD_LIMIT]}"
    )


def _norm(number: str) -> str:
    return re.sub(r"[\s,]", "", number).lower()


def _month(value: Any) -> str:
    if str(value).lower() == "present":
        return "Present"
    import datetime as dt

    year, month = str(value).split("-")[:2]
    return dt.date(int(year), int(month), 1).strftime("%b %Y")
