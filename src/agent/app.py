"""AgentCore Runtime entry point."""

from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langgraph_checkpoint_aws import AgentCoreMemorySaver

from agent.config import get_settings
from agent.supervisor import compile_all, handle

app = BedrockAgentCoreApp()
_settings = get_settings()
graphs = compile_all(AgentCoreMemorySaver(_settings.memory_id, region_name=_settings.region))


@app.entrypoint
def invoke(payload: dict, context=None) -> dict:
    return handle(graphs, payload)


if __name__ == "__main__":
    app.run()
