from agent.graphs.job_hunt import prefilter_score
from agent.sources import ashby, lever


def test_ashby_normalize_marks_remote():
    job = ashby.normalize(
        "acme",
        {
            "id": "1",
            "title": "AI Engineer",
            "location": "Pune",
            "isRemote": True,
            "secondaryLocations": [{"location": "Bengaluru"}],
            "jobUrl": "https://jobs/1",
            "descriptionPlain": "LangGraph",
            "publishedAt": "2026-10-01",
        },
    )
    assert job["id"] == "ashby-acme-1"
    assert job["location"] == "Pune, Bengaluru, Remote"


def test_lever_normalize_includes_lists():
    job = lever.normalize(
        "acme",
        {
            "id": "2",
            "text": "ML Engineer",
            "categories": {"location": "Remote"},
            "descriptionPlain": "Intro",
            "lists": [{"text": "Requirements", "content": "Python"}],
            "hostedUrl": "https://jobs/2",
        },
    )
    assert "Requirements: Python" in job["description"]


def test_long_skill_list_does_not_dilute_prefilter():
    prefs = {
        "target_titles": ["AI Engineer"],
        "skills": [f"s{i}" for i in range(60)] + ["python"],
        "locations": ["remote"],
    }
    job = {
        "title": "AI Engineer",
        "location": "Remote",
        "description": " ".join(["python"] + [f"s{i}" for i in range(7)]),
    }
    assert prefilter_score(job, prefs) == 100
