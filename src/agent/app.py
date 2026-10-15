"""AgentCore Runtime entry point.

Each invocation starts (or resumes) a graph run in a background thread and returns at once. The
session reports HealthyBusy until the run finishes, so AgentCore keeps it alive for long runs
(such as building several apply packs) instead of the caller having to wait on them.
"""

from __future__ import annotations

import logging
import threading

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langgraph_checkpoint_aws import AgentCoreMemorySaver

from agent.config import get_settings
from agent.supervisor import compile_all, default_thread_id, handle

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)
app = BedrockAgentCoreApp()
_settings = get_settings()
graphs = compile_all(AgentCoreMemorySaver(_settings.memory_id, region_name=_settings.region))


def _run(payload: dict, task_id: int) -> None:
    try:
        log.info("run finished: %s", handle(graphs, payload))
    except Exception as e:
        log.exception("run failed: %s", payload.get("thread_id"))
        try:
            from agent.services import get_services

            get_services().store.log(payload.get("task", "agent"), f"Run failed: {e}")
        except Exception:
            log.exception("could not record the failure")
    finally:
        app.complete_async_task(task_id)


@app.entrypoint
def invoke(payload: dict, context=None) -> dict:
    payload = dict(payload)
    if payload.get("task") not in graphs:
        return {
            "accepted": False,
            "error": f"unknown task {payload.get('task')!r}; use one of {sorted(graphs)}",
        }
    payload.setdefault("thread_id", default_thread_id(payload.get("task", "run")))
    task_id = app.add_async_task(payload.get("task", "run"), {"thread_id": payload["thread_id"]})
    threading.Thread(target=_run, args=(payload, task_id), daemon=True).start()
    return {"accepted": True, "task": payload.get("task"), "thread_id": payload["thread_id"]}


if __name__ == "__main__":
    app.run()
