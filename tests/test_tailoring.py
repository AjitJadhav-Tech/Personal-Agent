"""The fact check: nothing reaches a resume or cover letter unless the profile supports it."""

from fakes import FakeModel

from agent.profile import load_master_profile
from agent.tailoring import mechanical_problems, tailor_resume, write_cover_letter

JOB = {
    "id": "j",
    "company": "acme",
    "title": "AI Engineer",
    "location": "Remote",
    "description": "Build agents. Our 40-person team ships weekly.",
    "url": "https://x",
}
FACTS = {"a": "Cut costs by 25% for 1000+ users with sub-500 ms latency."}


def _profile():
    load_master_profile.cache_clear()
    return load_master_profile()


def test_numbers_must_come_from_cited_facts():
    ok = {"text": "Cut costs 25% for 1000+ users at sub-500 ms.", "fact_ids": ["a"]}
    assert mechanical_problems(ok, FACTS, None, None) == []
    inflated = {"text": "Cut costs by 40%.", "fact_ids": ["a"]}
    assert "number '40%' is not in the cited facts" in mechanical_problems(
        inflated, FACTS, None, None
    )


def test_unknown_or_missing_citations_fail():
    assert mechanical_problems({"text": "x", "fact_ids": []}, FACTS, None, None)
    assert mechanical_problems({"text": "x", "fact_ids": ["made-up"]}, FACTS, None, None)


def test_bullet_must_cite_a_fact_from_its_own_role():
    owned = {"r1": ["a"], "r2": ["b"]}
    item = {"exp_id": "r2", "text": "x", "fact_ids": ["a"]}
    assert "cites a fact from a different role" in mechanical_problems(item, FACTS, owned, None)


def test_invented_metric_is_revised_then_falls_back_to_fact_wording():
    model = FakeModel()
    model.resume_bullets = [{"text": "Cut manual effort by 90%.", "fact_ids": ["nexus-results"]}]
    model.revision = [
        {"index": 0, "text": "Cut manual effort by 95%.", "fact_ids": ["nexus-results"]}
    ]
    resume = tailor_resume(model, _profile(), JOB)
    bullets = resume["experience"][0]["bullets"]
    assert bullets[0].startswith("Achieved a 70% reduction in manual effort")
    assert any(c.startswith("replaced with fact wording") for c in resume["checks"])
    assert "revise_items" in model.calls


def test_reviewer_flag_triggers_revision():
    model = FakeModel()
    model.unsupported = [{"index": 1, "problem": "claims Bedrock scope not in fact"}]
    model.revision = [
        {
            "index": 0,
            "text": "Designed and deployed an enterprise-grade agentic AI "
            "platform on AWS Bedrock AgentCore Runtime.",
            "fact_ids": ["nexus-platform"],
        }
    ]
    resume = tailor_resume(model, _profile(), JOB)
    assert resume["checks"].count("passed") == len(resume["checks"])


def test_skills_are_limited_to_the_profile():
    resume = tailor_resume(FakeModel(), _profile(), JOB)
    assert "Rust" not in resume["skills"]
    assert resume["skills"][:2] == ["LangGraph", "Python"]


def test_every_role_stays_on_the_resume():
    resume = tailor_resume(FakeModel(), _profile(), JOB)
    assert [e["company"] for e in resume["experience"]] == [
        "C.G. IT Services LLC",
        "C.G. IT Services LLC",
        "Duroshox Pvt. Ltd.",
        "AllOps Technologies Pvt. Ltd.",
    ]
    assert all(e["bullets"] for e in resume["experience"])


def test_cover_letter_may_cite_the_job_description():
    letter = write_cover_letter(FakeModel(), _profile(), JOB)
    assert letter["paragraphs"][1] == "Your team is hiring for this role."
