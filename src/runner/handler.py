"""Starts or resumes a graph run on AgentCore Runtime.

Invoked by EventBridge Scheduler (start a task) and, asynchronously, by the approval API (resume a
paused run with the owner's decision). Keeping this separate lets the approval page answer
immediately while the agent works.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3

_client = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def session_id_for(thread_id: str) -> str:
    # Runtime session ids must be at least 33 characters; derive a stable one per thread.
    return f"{thread_id}-{hashlib.sha256(thread_id.encode()).hexdigest()[:32]}"[:100]


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    payload = {k: v for k, v in event.items() if k in ("task", "thread_id", "decision", "input")}
    if "thread_id" not in payload:
        # Same format as agent.supervisor.default_thread_id (not imported: Lambda has no langgraph).
        payload["thread_id"] = f"{payload['task']}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    resp = _client.invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
        runtimeSessionId=session_id_for(payload["thread_id"]),
        payload=json.dumps(payload).encode(),
    )
    body = resp["response"].read().decode()
    print(json.dumps({"payload": payload, "result": body[:2000]}))
    return {"thread_id": payload["thread_id"], "result": body}
