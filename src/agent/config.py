"""Runtime settings, read from environment variables set by the CDK stack."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    region: str
    table_name: str
    memory_id: str
    notify_email: str
    sender_email: str
    approval_base_url: str
    approval_secret_arn: str
    drafting_model_id: str
    fast_model_id: str
    dry_run: bool
    paused_parameter: str
    linkedin_client_id: str = ""
    linkedin_client_secret_name: str = ""
    linkedin_token_secret_arn: str = ""
    linkedin_api_version: str = "202606"


@lru_cache
def get_settings() -> Settings:
    env = os.environ
    return Settings(
        region=env.get("AWS_REGION", "us-east-1"),
        table_name=env.get("TABLE_NAME", ""),
        memory_id=env.get("MEMORY_ID", ""),
        notify_email=env.get("NOTIFY_EMAIL", ""),
        sender_email=env.get("SENDER_EMAIL", env.get("NOTIFY_EMAIL", "")),
        approval_base_url=env.get("APPROVAL_BASE_URL", "").rstrip("/"),
        approval_secret_arn=env.get("APPROVAL_SECRET_ARN", ""),
        # Kimi K3 through the US cross-Region inference profile (requests stay in US regions;
        # us-east-1 has no in-Region endpoint for this model). Override to switch models.
        drafting_model_id=env.get("DRAFTING_MODEL_ID", "us.moonshotai.kimi-k3"),
        fast_model_id=env.get("FAST_MODEL_ID", "us.moonshotai.kimi-k3"),
        dry_run=env.get("DRY_RUN", "true").lower() == "true",
        paused_parameter=env.get("PAUSED_PARAMETER", "/personal-agent/paused"),
        linkedin_client_id=env.get("LINKEDIN_CLIENT_ID", ""),
        linkedin_client_secret_name=env.get("LINKEDIN_CLIENT_SECRET_NAME", ""),
        linkedin_token_secret_arn=env.get("LINKEDIN_TOKEN_SECRET_ARN", ""),
        # LinkedIn API version (YYYYMM); publishing steps back a month if this one is retired.
        linkedin_api_version=env.get("LINKEDIN_API_VERSION", "202606"),
    )
