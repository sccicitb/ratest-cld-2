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
import base64
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from app.chat.client import ModelChunk, ModelClient
from app.chat.events import artifact, done, error, reasoning, step, token
from app.chat.prompt import system_message
from app.config import settings
from app.models import ArtifactVersion, Attachment, ChatSession, Message
from app.storage import open_blob
from app.tools.context import ToolContext
from app.tools.registry import ToolError, ToolRegistry

TITLE_MAX_LEN = 40


# ---------------------------------------------------------------------------
# Vision helpers (V2)
# ---------------------------------------------------------------------------


def _is_image_att(att: Attachment) -> bool:
    return att.file_type.startswith("image/")


def _image_block(att: Attachment) -> dict:
    with open_blob(att.url) as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{att.file_type};base64,{b64}"}}


def _content(text: str, attachments: list[Attachment]) -> str | list:
    """Return a plain string when no image attachments; otherwise an OpenAI content array."""
    image_atts = [a for a in attachments if _is_image_att(a)]
    if not image_atts:
        return text
    blocks: list[dict] = [{"type": "text", "text": text}]
    blocks.extend(_image_block(a) for a in image_atts)
    return blocks


def _cap_images(messages: list[dict], cap: int) -> list[dict]:
    """Keep only the most-recent `cap` image_url blocks across all messages.

    Walks newest→oldest.  Blocks beyond the budget are dropped.  If a message
    loses all its image blocks and its content-array collapses to just a text
    block, that message's content reverts to the plain text string.
    """
    # Count total images to decide whether capping is needed.
    def _count_images(msgs: list[dict]) -> int:
        total = 0
        for m in msgs:
            c = m.get("content")
            if isinstance(c, list):
                total += sum(1 for b in c if isinstance(b, dict) and b.get("type") == "image_url")
        return total

    if _count_images(messages) <= cap:
        return messages

    budget = cap
    result: list[dict] = []
    for msg in reversed(messages):
        c = msg.get("content")
        if not isinstance(c, list):
            result.append(msg)
            continue
        new_blocks: list[dict] = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "image_url":
                if budget > 0:
                    new_blocks.append(block)
                    budget -= 1
                # else drop the block
            else:
                new_blocks.append(block)
        # Collapse a content-array that has no image blocks left.
        image_count = sum(1 for b in new_blocks if isinstance(b, dict) and b.get("type") == "image_url")
        if image_count == 0:
            # Only a text block (or nothing) remains — collapse to plain string.
            text_blocks = [b for b in new_blocks if isinstance(b, dict) and b.get("type") == "text"]
            # Always collapse to a string. `_content` always emits a leading text
            # block, so text_blocks[0] is the normal case; the "" fallback guards
            # against ever emitting a malformed array-with-no-images.
            plain = text_blocks[0]["text"] if text_blocks else ""
            result.append({**msg, "content": plain})
        else:
            result.append({**msg, "content": new_blocks})

    return list(reversed(result))


# ---------------------------------------------------------------------------


def _auto_title(message: str) -> str:
    stripped = message.strip()
    if len(stripped) <= TITLE_MAX_LEN:
        return stripped
    return stripped[:TITLE_MAX_LEN].rstrip() + "…"


def _history_messages(session: ChatSession) -> list[dict]:
    """Map persisted `Message`s (oldest-first) to OpenAI `{role, content}`.

    User messages that had image attachments re-emit their image blocks so the
    model can still see them on follow-up turns (V2 vision re-feed).
    """
    out = []
    for m in session.messages:
        atts: list[Attachment] = m.attachments or []
        out.append({"role": m.role, "content": _content(m.content, atts)})
    return out


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
    model_content: str | None = None,
    attachment_ids: list[str] | None = None,
) -> AsyncIterator[dict]:
    """Run one chat turn.

    `message` is the user's text — persisted and shown in the bubble.
    `model_content` is what the model actually sees (e.g. with inline
    attachment text prepended); defaults to `message`. `attachment_ids` are
    bound to the new user `Message` so the frontend renders their chips.
    """
    try:
        # Snapshot prior history before adding this turn's user message.
        history = _history_messages(session)

        user_msg = Message(session_id=session.id, role="user", content=message)
        db.add(user_msg)
        db.flush()  # assign user_msg.id before linking attachments
        if attachment_ids:
            for aid in attachment_ids:
                att = db.get(Attachment, aid)
                # Only bind attachments that belong to this session's turn and
                # aren't already attached to an earlier message.
                if att is not None and att.message_id is None:
                    att.message_id = user_msg.id
        if session.title == "New Chat":
            session.title = _auto_title(message)
        session.updated_at = datetime.now(timezone.utc)
        db.commit()

        # Build this turn's user message, including any image attachments.
        current_image_atts: list[Attachment] = []
        if attachment_ids:
            for aid in attachment_ids:
                att = db.get(Attachment, aid)
                if att is not None and _is_image_att(att):
                    current_image_atts.append(att)
        current_content = _content(model_content or message, current_image_atts)
        messages = history + [{"role": "user", "content": current_content}]
        messages = _cap_images(messages, settings.max_vision_images_per_turn)
        # Prepended after capping so `_cap_images` keeps operating on exactly
        # the user/assistant turns it was written for. Index 0 for the rest of
        # the turn: the loop only ever appends, so the persona still applies on
        # the iteration that composes the post-tool answer.
        sys_msg = system_message()
        if sys_msg is not None:
            messages = [sys_msg] + messages

        yield step("thinking", "active")

        final_text_parts: list[str] = []
        iteration = 0
        force_final = False
        current_turn_artifact_versions: list[str] = []

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
                    # Stream the answer live (time-to-first-token). Interim text
                    # on tool-calling turns is cleared client-side on tool-start;
                    # only the final turn's text is persisted (see below).
                    if chunk.text:
                        yield token(chunk.text)
                elif chunk.type == "reasoning":
                    yield reasoning(chunk.text or "")
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
                # Text already streamed live above; just record it for persistence.
                final_text_parts.append("".join(text_parts))
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

            # v1.1 Stage A1: after a successful `create_artifact` tool call,
            # emit the artifact SSE event and track versions for later
            # linkage to the assistant message.
            if ctx.pending_artifacts:
                for info in ctx.pending_artifacts:
                    yield artifact(
                        artifactId=info["artifact_id"],
                        version=info["version"],
                        title=info["title"],
                    )
                    current_turn_artifact_versions.append(info["version_id"])
                ctx.pending_artifacts.clear()

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
        db.flush()  # assign assistant_msg.id so we can link artifacts

        # v1.1 Stage A1: link artifact versions created this turn to the
        # assistant message that authored them.
        for version_id in current_turn_artifact_versions:
            v = db.get(ArtifactVersion, version_id)
            if v is not None:
                v.message_id = assistant_msg.id

        session.updated_at = datetime.now(timezone.utc)
        db.commit()

        yield done(message_id=assistant_msg.id)
    except Exception as exc:  # noqa: BLE001 - last-resort guard, never raise out of the generator
        yield error(f"Chat turn failed: {exc}")


def _dump_args(arguments: dict | None) -> str:
    return json.dumps(arguments or {})
