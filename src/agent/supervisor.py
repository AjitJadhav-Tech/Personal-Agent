"""Routes a task to its graph. Each task runs on its own LangGraph thread so a paused approval in
one workflow never blocks another."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agent.graphs import content, daily_report, job_hunt, opportunity

BUILDERS = {
    content.TASK: content.build,
    job_hunt.TASK: job_hunt.build,
    daily_report.TASK: daily_report.build,
    opportunity.TASK: opportunity.build,
}

ACTOR_ID = "owner"


def compile_all(checkpointer: BaseCheckpointSaver) -> dict[str, CompiledStateGraph]:
    return {task: build().compile(checkpointer=checkpointer) for task, build in BUILDERS.items()}


def default_thread_id(task: str, now: datetime | None = None) -> str:
    return f"{task}-{(now or datetime.now(UTC)).strftime('%Y%m%dT%H%M%S')}"


def handle(graphs: dict[str, CompiledStateGraph], payload: dict[str, Any]) -> dict[str, Any]:
    """Payload shapes:
    start:  {"task": "content", "input": {...}, "thread_id": optional}
    resume: {"task": "content", "thread_id": "...", "decision": {...}}
    """
    task = payload.get("task")
    if task not in graphs:
        raise ValueError(f"unknown task: {task!r}")
    graph = graphs[task]
    thread_id = payload.get("thread_id") or default_thread_id(task)
    config = {"configurable": {"thread_id": thread_id, "actor_id": ACTOR_ID}}
    if "decision" in payload:
        result = graph.invoke(Command(resume=payload["decision"]), config)
    else:
        result = graph.invoke(payload.get("input", {}), config)
    waiting = bool(graph.get_state(config).interrupts)
    return {
        "task": task,
        "thread_id": thread_id,
        "waiting_for_approval": waiting,
        "status": result.get("status", "waiting" if waiting else "done"),
    }
