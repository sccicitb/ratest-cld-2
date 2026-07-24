"""Model client abstraction over an OpenAI-compatible chat endpoint (§7, §12).

`run_turn` (`app/chat/loop.py`) only depends on the `ModelClient` Protocol, so
tests can swap in a scripted fake with no live LLM. `OpenAIModelClient` is the
real implementation and is smoke-tested manually against llama-server, not in
CI: the OpenAI streaming wire format fragments `tool_calls` across many chunks
(indexed by `tool_calls[].index`, with `function.arguments` arriving as
partial JSON strings), so this client accumulates those fragments per index
and only yields a normalized tool_call `ModelChunk` once the turn completes.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ModelChunk:
    """A normalized piece of a model turn: either a text delta or a completed tool call."""

    type: Literal["text", "tool_call", "reasoning"]
    text: str | None = None
    id: str | None = None
    name: str | None = None
    arguments: dict | None = None


@runtime_checkable
class ModelClient(Protocol):
    def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[ModelChunk]: ...


@dataclass
class _PendingToolCall:
    id: str = ""
    name: str = ""
    arguments: str = field(default="")


class OpenAIModelClient:
    """Real client: OpenAI-SDK-compatible, streaming, against `settings.model_base_url`."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.model_base_url, api_key=settings.model_api_key
        )

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[ModelChunk]:
        kwargs: dict = {
            "model": settings.model_name,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        pending: dict[int, _PendingToolCall] = {}

        response = await self._client.chat.completions.create(**kwargs)
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning is None:
                reasoning = (getattr(delta, "model_extra", None) or {}).get("reasoning_content")
            if reasoning:
                yield ModelChunk(type="reasoning", text=reasoning)

            if delta.content:
                yield ModelChunk(type="text", text=delta.content)

            for tc in delta.tool_calls or []:
                is_new = tc.index not in pending
                slot = pending.setdefault(tc.index, _PendingToolCall())
                if tc.id:
                    slot.id = tc.id
                elif is_new:
                    # Some servers omit `id` on the first fragment; fall back
                    # to a generated id so the `tool_call_id` echoed back to
                    # the model (app/chat/loop.py) is never an empty string.
                    slot.id = f"call_{tc.index}"
                if tc.function and tc.function.name:
                    slot.name = tc.function.name
                if tc.function and tc.function.arguments:
                    slot.arguments += tc.function.arguments

        for slot in pending.values():
            try:
                args = json.loads(slot.arguments) if slot.arguments else {}
            except json.JSONDecodeError:
                logger.warning(
                    "Tool call %r (id=%s) had non-JSON arguments; treating as {}: %r",
                    slot.name, slot.id, slot.arguments,
                )
                args = {}
            yield ModelChunk(type="tool_call", id=slot.id, name=slot.name, arguments=args)


def get_model_client() -> ModelClient:
    return OpenAIModelClient()
