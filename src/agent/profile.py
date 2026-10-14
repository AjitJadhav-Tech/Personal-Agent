"""Loads the owner's preferences (target roles, companies, locations)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "profile" / "preferences.yaml"


@lru_cache
def load_preferences(path: str | None = None) -> dict[str, Any]:
    p = Path(path or os.environ.get("PREFERENCES_PATH", DEFAULT_PATH))
    return yaml.safe_load(p.read_text()) or {}
