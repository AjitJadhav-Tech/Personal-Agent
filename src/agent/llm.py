"""Claude on Amazon Bedrock through the Anthropic SDK's Bedrock Mantle client."""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol


class Drafter(Protocol):
    def __call__(self, system: str, prompt: str) -> str: ...


@lru_cache
def _client(region: str):
    from anthropic import AnthropicBedrockMantle

    return AnthropicBedrockMantle(aws_region=region)


def bedrock_drafter(model_id: str, region: str) -> Drafter:
    def draft(system: str, prompt: str) -> str:
        response = _client(region).messages.create(
            model=model_id,
            max_tokens=16000,
            system=system,
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined to draft this item")
        return "".join(b.text for b in response.content if b.type == "text").strip()

    return draft
