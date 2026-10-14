"""Greenhouse public job board API (read-only, published by each company for this purpose)."""

from __future__ import annotations

import json
import re
import urllib.request
from html import unescape
from typing import Any

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


def fetch_board(board: str, timeout: float = 20) -> list[dict[str, Any]]:
    with urllib.request.urlopen(BOARD_URL.format(board=board), timeout=timeout) as resp:
        data = json.load(resp)
    return [normalize(board, j) for j in data.get("jobs", [])]


def normalize(board: str, job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"greenhouse-{board}-{job['id']}",
        "source": "greenhouse",
        "company": board,
        "title": job.get("title", ""),
        "location": (job.get("location") or {}).get("name", ""),
        "url": job.get("absolute_url", ""),
        "description": _strip_html(unescape(job.get("content", ""))),
        "updated_at": job.get("updated_at", ""),
    }


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
