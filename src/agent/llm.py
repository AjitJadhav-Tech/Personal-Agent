"""Text generation on Amazon Bedrock through the Converse API (Kimi K3 by default).

Each call is a single user turn, so Kimi K3's Converse caveat about replaying reasoning blocks
from earlier turns does not apply.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol


class Drafter(Protocol):
    def __call__(self, system: str, prompt: str) -> str: ...


@lru_cache
def _client(region: str):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(read_timeout=300, retries={"max_attempts": 4, "mode": "adaptive"}),
    )


def response_text(response: dict[str, Any]) -> str:
    """Joins the text blocks of a Converse response, skipping reasoning blocks."""
    if response.get("stopReason") in ("guardrail_intervened", "content_filtered"):
        raise RuntimeError(f"model declined to draft this item ({response['stopReason']})")
    blocks = response["output"]["message"]["content"]
    text = "".join(b["text"] for b in blocks if "text" in b).strip()
    if not text:
        raise RuntimeError("model returned no text")
    return text


def bedrock_drafter(model_id: str, region: str, max_tokens: int = 4096) -> Drafter:
    def draft(system: str, prompt: str) -> str:
        response = _client(region).converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens},
        )
        return response_text(response)

    return draft


def parse_json(text: str) -> Any:
    """Parses the JSON object a model returned, tolerating code fences or a short preamble."""
    import json

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("model did not return JSON") from None
        return json.loads(cleaned[start : end + 1])
