"""Persistence for approvals and the activity log (DynamoDB in AWS, in-memory for tests)."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from agent.approvals import DECIDED, PENDING, Approval


class AlreadyDecided(Exception):
    """Raised when an approval was already answered or does not exist."""


class Store(Protocol):
    def put_approval(self, approval: Approval) -> None: ...
    def get_approval(self, approval_id: str) -> Approval | None: ...
    def decide(self, approval_id: str, decision: dict[str, Any]) -> Approval: ...
    def log(self, kind: str, message: str, data: dict[str, Any] | None = None) -> None: ...
    def activity_since(self, since: datetime) -> list[dict[str, Any]]: ...


class InMemoryStore:
    def __init__(self) -> None:
        self.approvals: dict[str, Approval] = {}
        self.activity: list[dict[str, Any]] = []

    def put_approval(self, approval: Approval) -> None:
        self.approvals[approval.approval_id] = approval

    def get_approval(self, approval_id: str) -> Approval | None:
        return self.approvals.get(approval_id)

    def decide(self, approval_id: str, decision: dict[str, Any]) -> Approval:
        approval = self.approvals.get(approval_id)
        if approval is None or approval.status != PENDING:
            raise AlreadyDecided(approval_id)
        approval.status = DECIDED
        approval.decision = decision
        return approval

    def log(self, kind: str, message: str, data: dict[str, Any] | None = None) -> None:
        self.activity.append(
            {
                "at": datetime.now(UTC).isoformat(),
                "kind": kind,
                "message": message,
                "data": data or {},
            }
        )

    def activity_since(self, since: datetime) -> list[dict[str, Any]]:
        return [a for a in self.activity if datetime.fromisoformat(a["at"]) >= since]


class DynamoStore:
    """Single-table layout.

    APPROVAL#<id> / META            one approval
    ACTIVITY#<yyyy-mm-dd> / <ts>#<id> activity entries, partitioned by UTC day
    """

    def __init__(self, table_name: str, region: str) -> None:
        import boto3

        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def put_approval(self, approval: Approval) -> None:
        item = {"pk": f"APPROVAL#{approval.approval_id}", "sk": "META", **_encode(asdict(approval))}
        item["ttl"] = int(datetime.fromisoformat(approval.expires_at).timestamp()) + 30 * 86400
        self.table.put_item(Item=item)

    def get_approval(self, approval_id: str) -> Approval | None:
        item = self.table.get_item(Key={"pk": f"APPROVAL#{approval_id}", "sk": "META"}).get("Item")
        return _approval_from_item(item) if item else None

    def decide(self, approval_id: str, decision: dict[str, Any]) -> Approval:
        from botocore.exceptions import ClientError

        try:
            item = self.table.update_item(
                Key={"pk": f"APPROVAL#{approval_id}", "sk": "META"},
                UpdateExpression="SET #s = :decided, decision = :d",
                ConditionExpression="attribute_exists(pk) AND #s = :pending",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":decided": DECIDED,
                    ":pending": PENDING,
                    ":d": json.dumps(decision),
                },
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise AlreadyDecided(approval_id) from e
            raise
        return _approval_from_item(item)

    def log(self, kind: str, message: str, data: dict[str, Any] | None = None) -> None:
        now = datetime.now(UTC)
        self.table.put_item(
            Item={
                "pk": f"ACTIVITY#{now.date().isoformat()}",
                "sk": f"{now.isoformat()}#{uuid.uuid4().hex[:8]}",
                "at": now.isoformat(),
                "kind": kind,
                "message": message,
                "data": json.dumps(data or {}),
                "ttl": int((now + timedelta(days=400)).timestamp()),
            }
        )

    def activity_since(self, since: datetime) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        out: list[dict[str, Any]] = []
        day = since.date()
        while day <= datetime.now(UTC).date():
            resp = self.table.query(
                KeyConditionExpression=Key("pk").eq(f"ACTIVITY#{day.isoformat()}")
                & Key("sk").gte(since.isoformat())
            )
            for item in resp["Items"]:
                out.append({**item, "data": json.loads(item.get("data", "{}"))})
            day += timedelta(days=1)
        return out


def _encode(d: dict[str, Any]) -> dict[str, Any]:
    return {k: json.dumps(v) if isinstance(v, dict) else v for k, v in d.items()}


def _approval_from_item(item: dict[str, Any]) -> Approval:
    return Approval(
        approval_id=item["approval_id"],
        kind=item["kind"],
        task=item["task"],
        thread_id=item["thread_id"],
        title=item["title"],
        preview=json.loads(item["preview"]),
        created_at=item["created_at"],
        expires_at=item["expires_at"],
        status=item["status"],
        decision=json.loads(item["decision"]) if item.get("decision") else {},
    )
