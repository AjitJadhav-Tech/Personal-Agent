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
    def seen_jobs(self, job_ids: list[str]) -> set[str]: ...
    def mark_seen(self, job_ids: list[str]) -> None: ...
    def put_job(self, job: dict[str, Any]) -> None: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    def pending_approvals(self) -> list[Approval]: ...
    def put_opportunity(self, opp: dict[str, Any]) -> None: ...
    def list_opportunities(self, limit: int = 20) -> list[dict[str, Any]]: ...


class InMemoryStore:
    def __init__(self) -> None:
        self.approvals: dict[str, Approval] = {}
        self.activity: list[dict[str, Any]] = []
        self.seen: set[str] = set()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.opportunities: dict[str, dict[str, Any]] = {}

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

    def seen_jobs(self, job_ids: list[str]) -> set[str]:
        return self.seen & set(job_ids)

    def mark_seen(self, job_ids: list[str]) -> None:
        self.seen |= set(job_ids)

    def put_job(self, job: dict[str, Any]) -> None:
        self.jobs[job["id"]] = job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def pending_approvals(self) -> list[Approval]:
        return sorted(
            (a for a in self.approvals.values() if a.status == PENDING and not a.is_expired()),
            key=lambda a: a.created_at,
            reverse=True,
        )

    def put_opportunity(self, opp: dict[str, Any]) -> None:
        self.opportunities[opp["id"]] = dict(opp)

    def list_opportunities(self, limit: int = 20) -> list[dict[str, Any]]:
        opps = sorted(self.opportunities.values(), key=lambda o: o["created_at"], reverse=True)
        return opps[:limit]


class DynamoStore:
    """Single-table layout.

    APPROVAL#<id> / META            one approval
    ACTIVITY#<yyyy-mm-dd> / <ts>#<id> activity entries, partitioned by UTC day
    SEEN#<job id> / META            a job already considered, so later runs skip it
    JOB#<job id> / META             a shortlisted job with its full description
    PENDING / <approval id>         index of approvals still waiting, for the web app
    OPP / <created at>#<id>         an opportunity the owner pasted in, with the drafted reply
    """

    def __init__(self, table_name: str, region: str) -> None:
        import boto3

        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def put_approval(self, approval: Approval) -> None:
        item = {"pk": f"APPROVAL#{approval.approval_id}", "sk": "META", **_encode(asdict(approval))}
        item["ttl"] = int(datetime.fromisoformat(approval.expires_at).timestamp()) + 30 * 86400
        self.table.put_item(Item=item)
        if approval.status == PENDING:
            self.table.put_item(
                Item={
                    "pk": "PENDING",
                    "sk": approval.approval_id,
                    "ttl": int(datetime.fromisoformat(approval.expires_at).timestamp()),
                }
            )

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
        self.table.delete_item(Key={"pk": "PENDING", "sk": approval_id})
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

    def seen_jobs(self, job_ids: list[str]) -> set[str]:
        seen: set[str] = set()
        client = self.table.meta.client
        unique = list(dict.fromkeys(job_ids))
        for i in range(0, len(unique), 100):
            keys = [{"pk": f"SEEN#{j}", "sk": "META"} for j in unique[i : i + 100]]
            request = {self.table.name: {"Keys": keys, "ProjectionExpression": "pk"}}
            while request:
                resp = client.batch_get_item(RequestItems=request)
                seen |= {item["pk"][5:] for item in resp["Responses"].get(self.table.name, [])}
                request = resp.get("UnprocessedKeys") or None
        return seen

    def mark_seen(self, job_ids: list[str]) -> None:
        expires = int((datetime.now(UTC) + timedelta(days=120)).timestamp())
        with self.table.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
            for job_id in dict.fromkeys(job_ids):
                batch.put_item(Item={"pk": f"SEEN#{job_id}", "sk": "META", "ttl": expires})

    def put_job(self, job: dict[str, Any]) -> None:
        expires = int((datetime.now(UTC) + timedelta(days=60)).timestamp())
        self.table.put_item(
            Item={"pk": f"JOB#{job['id']}", "sk": "META", "job": json.dumps(job), "ttl": expires}
        )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": f"JOB#{job_id}", "sk": "META"}).get("Item")
        return json.loads(item["job"]) if item else None

    def pending_approvals(self) -> list[Approval]:
        from boto3.dynamodb.conditions import Key

        ids = [
            i["sk"]
            for i in self.table.query(KeyConditionExpression=Key("pk").eq("PENDING"))["Items"]
        ]
        approvals = [a for a in map(self.get_approval, ids) if a]
        return sorted(
            (a for a in approvals if a.status == PENDING and not a.is_expired()),
            key=lambda a: a.created_at,
            reverse=True,
        )

    def put_opportunity(self, opp: dict[str, Any]) -> None:
        expires = int((datetime.now(UTC) + timedelta(days=180)).timestamp())
        self.table.put_item(
            Item={
                "pk": "OPP",
                "sk": f"{opp['created_at']}#{opp['id']}",
                "opp": json.dumps(opp),
                "ttl": expires,
            }
        )

    def list_opportunities(self, limit: int = 20) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        resp = self.table.query(
            KeyConditionExpression=Key("pk").eq("OPP"), ScanIndexForward=False, Limit=limit
        )
        return [json.loads(i["opp"]) for i in resp["Items"]]


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
