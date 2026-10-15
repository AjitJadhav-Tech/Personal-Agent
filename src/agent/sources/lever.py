"""Lever public postings API (read-only, published by each company for this purpose)."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

BOARD_URL = "https://api.lever.co/v0/postings/{board}?mode=json"


def fetch_board(board: str, timeout: float = 20) -> list[dict[str, Any]]:
    with urllib.request.urlopen(BOARD_URL.format(board=board), timeout=timeout) as resp:
        data = json.load(resp)
    return [normalize(board, j) for j in data]


def normalize(board: str, job: dict[str, Any]) -> dict[str, Any]:
    categories = job.get("categories") or {}
    locations = [categories.get("location") or ""] + list(categories.get("allLocations") or [])
    if job.get("workplaceType") == "remote":
        locations.append("Remote")
    sections = [
        f"{section.get('text', '')}: {section.get('content', '')}"
        for section in job.get("lists") or []
    ]
    description = "\n".join(
        part
        for part in [job.get("descriptionPlain", ""), *sections, job.get("additionalPlain", "")]
        if part
    )
    return {
        "id": f"lever-{board}-{job['id']}",
        "source": "lever",
        "company": board,
        "title": job.get("text", ""),
        "location": ", ".join(dict.fromkeys(loc for loc in locations if loc)),
        "url": job.get("hostedUrl") or job.get("applyUrl", ""),
        "description": description.strip(),
        "updated_at": str(job.get("createdAt", "")),
    }
