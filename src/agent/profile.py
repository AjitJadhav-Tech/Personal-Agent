"""Loads the owner's preferences and master profile (the fact bank)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROFILE_DIR = Path(__file__).resolve().parents[2] / "profile"


@lru_cache
def load_preferences(path: str | None = None) -> dict[str, Any]:
    p = Path(path or os.environ.get("PREFERENCES_PATH", PROFILE_DIR / "preferences.yaml"))
    return yaml.safe_load(p.read_text()) or {}


@lru_cache
def load_master_profile(path: str | None = None) -> dict[str, Any]:
    p = Path(path or os.environ.get("MASTER_PROFILE_PATH", PROFILE_DIR / "master_profile.yaml"))
    return yaml.safe_load(p.read_text()) or {}


def fact_index(profile: dict[str, Any]) -> dict[str, str]:
    """Every citable fact by id: experience facts plus objective, years, certs and education."""
    facts: dict[str, str] = {}
    basics = profile.get("basics", {})
    if profile.get("objective"):
        facts["objective"] = profile["objective"]
    if basics.get("years_experience"):
        facts["years"] = f"{basics['years_experience']} years of experience"
    for exp in profile.get("experience", []):
        for fact in exp.get("facts", []):
            facts[fact["id"]] = fact["text"]
        for project in exp.get("projects", []):
            for fact in project.get("facts", []):
                facts[fact["id"]] = (
                    f"{project['name']} ({project.get('client', '')}): {fact['text']}"
                )
    for cert in profile.get("certifications", []):
        facts[cert["id"]] = cert["name"]
    for edu in profile.get("education", []):
        facts[edu["id"]] = f"{edu['degree']}, {edu['institution']}, {edu['year']}"
    return facts


def plain_facts(profile: dict[str, Any]) -> dict[str, str]:
    """Fact id -> its sentence alone (no project prefix), for use directly on a resume."""
    out = dict(fact_index(profile))
    for exp in profile.get("experience", []):
        for project in exp.get("projects", []):
            for fact in project.get("facts", []):
                out[fact["id"]] = fact["text"]
    return out


def all_skills(profile: dict[str, Any]) -> list[str]:
    return [s for group in (profile.get("skills") or {}).values() for s in group]


def facts_by_experience(profile: dict[str, Any]) -> dict[str, list[str]]:
    """Experience id -> the fact ids that belong to it."""
    out: dict[str, list[str]] = {}
    for exp in profile.get("experience", []):
        ids = [f["id"] for f in exp.get("facts", [])]
        for project in exp.get("projects", []):
            ids += [f["id"] for f in project.get("facts", [])]
        out[exp["id"]] = ids
    return out
