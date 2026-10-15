"""Opportunity records, kept free of LangGraph so the approval Lambda can create them."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any


def new_record(text: str, link: str = "", documents: bool = False) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(UTC).isoformat(),
        "text": text,
        "link": link,
        "documents": documents,
        "status": "drafting",
    }
