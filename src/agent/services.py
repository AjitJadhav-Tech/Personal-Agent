"""Wires the graphs to AWS services, with a hook to swap in fakes for tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from agent import linkedin
from agent.config import Settings, get_settings
from agent.llm import Drafter, bedrock_drafter
from agent.notify import Notifier, SesNotifier
from agent.store import DynamoStore, Store


class LinkedInAccount:
    """The owner's LinkedIn connection, kept in a Secrets Manager secret."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _client(self):
        import boto3

        return boto3.client("secretsmanager", region_name=self.settings.region)

    def record(self) -> dict | None:
        if not self.settings.linkedin_token_secret_arn:
            return None
        raw = self._client().get_secret_value(SecretId=self.settings.linkedin_token_secret_arn)
        return linkedin.parse_token_record(raw.get("SecretString"))

    def save(self, record: dict) -> None:
        self._client().put_secret_value(
            SecretId=self.settings.linkedin_token_secret_arn, SecretString=json.dumps(record)
        )

    def client_secret(self) -> str:
        raw = _secret(self.settings.linkedin_client_secret_name, self.settings.region).strip()
        # Accept a key/value secret too: the console's default type stores {"key": "value"}.
        try:
            parsed = json.loads(raw)
        except ValueError:
            return raw
        if isinstance(parsed, dict) and len(parsed) == 1:
            return str(next(iter(parsed.values()))).strip()
        return raw

    def publish(self, text: str) -> str:
        record = self.record()
        if record is None:
            raise linkedin.LinkedInError("LinkedIn is not connected")
        return linkedin.publish_post(
            record["access_token"], record["person_urn"], text, self.settings.linkedin_api_version
        )


@dataclass
class Services:
    settings: Settings
    store: Store
    notifier: Notifier
    drafter: Drafter
    approval_secret: str
    linkedin: LinkedInAccount | None = None

    def is_paused(self) -> bool:
        return _ssm_flag(self.settings.paused_parameter, self.settings.region)

    def set_paused(self, paused: bool) -> None:
        import boto3

        boto3.client("ssm", region_name=self.settings.region).put_parameter(
            Name=self.settings.paused_parameter,
            Value="true" if paused else "false",
            Type="String",
            Overwrite=True,
        )


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
        linkedin=LinkedInAccount(s),
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
