import pytest

from agent.llm import response_text


def _resp(blocks, stop="end_turn"):
    return {"stopReason": stop, "output": {"message": {"role": "assistant", "content": blocks}}}


def test_reasoning_blocks_are_skipped():
    blocks = [{"reasoningContent": {"reasoningText": {"text": "thinking"}}}, {"text": "Post body"}]
    assert response_text(_resp(blocks)) == "Post body"


def test_filtered_or_empty_responses_raise():
    with pytest.raises(RuntimeError):
        response_text(_resp([{"text": "x"}], stop="content_filtered"))
    with pytest.raises(RuntimeError):
        response_text(_resp([]))
