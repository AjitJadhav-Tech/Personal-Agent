"""Wires the graphs to AWS services, with a hook to swap in fakes for tests."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from agent.config import Settings, get_settings
from agent.llm import Drafter, bedrock_drafter
from agent.notify import Notifier, SesNotifier
from agent.store import DynamoStore, Store


@dataclass
class Services:
    settings: Settings
    store: Store
    notifier: Notifier
    drafter: Drafter
    approval_secret: str

    def is_paused(self) -> bool:
        return _ssm_flag(self.settings.paused_parameter, self.settings.region)


_override: Services | None = None


def set_services(services: Services | None) -> None:
    """Tests call this to inject in-memory fakes."""
    global _override
    _override = services


def get_services() -> Services:
    return _override or _aws_services()


@lru_cache
def _aws_services() -> Services:
    s = get_settings()
    return Services(
        settings=s,
        store=DynamoStore(s.table_name, s.region),
        notifier=SesNotifier(sender=s.sender_email, recipient=s.notify_email, region=s.region),
        drafter=bedrock_drafter(s.drafting_model_id, s.region),
        approval_secret=_secret(s.approval_secret_arn, s.region),
    )


def _secret(arn: str, region: str) -> str:
    import boto3

    return boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=arn)[
        "SecretString"
    ]


def _ssm_flag(name: str, region: str) -> bool:
    import boto3

    try:
        value = boto3.client("ssm", region_name=region).get_parameter(Name=name)["Parameter"][
            "Value"
        ]
    except Exception:
        # Fail closed: if the kill switch cannot be read, treat the agent as paused.
        return True
    return value.strip().lower() == "true"
