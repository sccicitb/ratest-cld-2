"""The agentic chat tool-use loop (§7) — `run_turn` is the manual loop.

Persists the user message, talks to the model, dispatches any tool calls
through the registry, and yields `StreamEvent` dicts (`app/chat/events.py`)
the whole way. The HTTP route (next dispatch) just turns this generator into
an SSE response.

Locked algorithm — see `.superpowers/sdd/stage-5-brief.md` §7:
1. Persist user `Message`; auto-title a "New Chat"; build OpenAI-shaped
   `messages` from history + this turn; emit `thinking active`.
2. `model.stream(messages, registry.schemas())`.
3. No tool calls -> stream the answer as `token`s (only on this kind of
   turn); break.
4. Tool calls -> emit a `calling_tool active`/`complete` pair per call (with
   a unique `id` and a server-injected `toolArgs.scope`), run them
   concurrently (capped), append the assistant tool-call message + one
   `role: tool` message per result, loop to 2. Capped by
   `settings.max_tool_iterations`; on the cap, force one final answer with
   no tools offered.
5. Persist the assistant `Message`; emit `done`.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from app.chat.client import ModelChunk, ModelClient
from app.chat.events import done, error, step, token
from app.config import settings
from app.models import ChatSession, Message
from app.tools.context import ToolContext
from app.tools.registry import ToolError, ToolRegistry

TITLE_MAX_LEN = 40


def _auto_title(message: str) -> str:
    stripped = message.strip()
    if len(stripped) <= TITLE_MAX_LEN:
        return stripped
    return stripped[:TITLE_MAX_LEN].rstrip() + "…"


def _history_messages(session: ChatSession) -> list[dict]:
    """Map persisted `Message`s (oldest-first) to OpenAI `{role, content}`."""
    return [{"role": m.role, "content": m.content} for m in session.messages]


def _scope_label(ctx: ToolContext) -> str:
    # Display hint only — never derived from or passed to the model. Per the
    # brief, the simplest acceptable rule: a session implies "+ KB" scope.
    return "this chat + KB" if ctx.session_id else "KB"


async def _run_tool_call(
    name: str, args: dict, registry: ToolRegistry, ctx: ToolContext
) -> str:
    try:
        return await registry.execute(name, args, ctx)
    except ToolError as exc:
        return f"Tool error: {exc}"


async def run_turn(
    *,
    db,
    session: ChatSession,
    message: str,
    registry: ToolRegistry,
    model: ModelClient,
    ctx: ToolContext,
) -> AsyncIterator[dict]:
    try:
        # Snapshot prior history before adding this turn's user message.
        history = _history_messages(session)

        user_msg = Message(session_id=session.id, role="user", content=message)
        db.add(user_msg)
        if session.title == "New Chat":
            session.title = _auto_title(message)
        session.updated_at = datetime.now(timezone.utc)
        db.commit()

        messages = history + [{"role": "user", "content": message}]

        yield step("thinking", "active")

        final_text_parts: list[str] = []
        iteration = 0
        force_final = False

        while True:
            iteration += 1
            # Once we've used up the allowed model<->tool rounds, offer no
            # tools so the model (or our own fallback below) must answer.
            force_final = force_final or iteration > settings.max_tool_iterations
            tools = [] if force_final else registry.schemas()

            text_parts: list[str] = []
            tool_calls: list[ModelChunk] = []
            async for chunk in model.stream(messages, tools):
                if chunk.type == "text":
                    text_parts.append(chunk.text or "")
                elif chunk.type == "tool_call":
                    tool_calls.append(chunk)

            if not tool_calls or force_final:
                # Final answer turn — either the model stopped calling tools,
                # or we've hit the iteration cap and force one final answer
                # regardless of what the model tried to do (a misbehaving
                # model that still emits a tool_call with no tools offered
                # is ignored here; we already have nothing else to act on).
                yield step("thinking", "complete")
                yield step("generating_response", "active")
                full_text = "".join(text_parts)
                if full_text:
                    yield token(full_text)
                final_text_parts.append(full_text)
                yield step("generating_response", "complete")
                break

            # Tool-calling turn: suppress any interim text per the
            # interleaving rule, append the assistant tool-call message,
            # run the calls (capped concurrency), emit step events.
            assistant_tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": _dump_args(tc.arguments)},
                }
                for tc in tool_calls
            ]
            messages.append(
                {"role": "assistant", "content": None, "tool_calls": assistant_tool_calls}
            )

            call_ids = [str(uuid.uuid4()) for _ in tool_calls]
            scope = _scope_label(ctx)
            for tc, call_id in zip(tool_calls, call_ids):
                yield step(
                    "calling_tool",
                    "active",
                    id=call_id,
                    tool_name=tc.name,
                    tool_args={**(tc.arguments or {}), "scope": scope},
                )

            semaphore = asyncio.Semaphore(settings.max_parallel_tools)

            async def _bounded(tc: ModelChunk) -> str:
                async with semaphore:
                    return await _run_tool_call(tc.name, tc.arguments or {}, registry, ctx)

            results = await asyncio.gather(*(_bounded(tc) for tc in tool_calls))

            for call_id in call_ids:
                yield step("calling_tool", "complete", id=call_id)

            for tc, result in zip(tool_calls, results):
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
            # Loop back to step 2.

        final_text = "".join(final_text_parts)
        assistant_msg = Message(
            session_id=session.id, role="assistant", content=final_text
        )
        db.add(assistant_msg)
        session.updated_at = datetime.now(timezone.utc)
        db.commit()

        yield done(message_id=assistant_msg.id)
    except Exception as exc:  # noqa: BLE001 - last-resort guard, never raise out of the generator
        yield error(f"Chat turn failed: {exc}")


def _dump_args(arguments: dict | None) -> str:
    return json.dumps(arguments or {})
