"""Ashby public job board API (read-only, published by each company for this purpose)."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{board}"


def fetch_board(board: str, timeout: float = 20) -> list[dict[str, Any]]:
    with urllib.request.urlopen(BOARD_URL.format(board=board), timeout=timeout) as resp:
        data = json.load(resp)
    return [normalize(board, j) for j in data.get("jobs", []) if j.get("isListed", True)]


def normalize(board: str, job: dict[str, Any]) -> dict[str, Any]:
    locations = [job.get("location") or ""]
    locations += [s.get("location", "") for s in job.get("secondaryLocations") or []]
    if job.get("isRemote"):
        locations.append("Remote")
    return {
        "id": f"ashby-{board}-{job['id']}",
        "source": "ashby",
        "company": board,
        "title": job.get("title", ""),
        "location": ", ".join(loc for loc in locations if loc),
        "url": job.get("jobUrl") or job.get("applyUrl", ""),
        "description": (job.get("descriptionPlain") or "").strip(),
        "updated_at": job.get("publishedAt", ""),
    }
