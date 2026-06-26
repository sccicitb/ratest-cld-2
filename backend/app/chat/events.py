"""`StreamEvent` builders — must match `src/types/chat.ts` byte-for-byte.

Every event the chat loop yields goes through one of these so the camelCase
keys (`toolName`, `toolArgs`, `messageId`) and optional-field omission stay
correct in one place rather than scattered across the loop.
"""
from __future__ import annotations


def step(
    step: str,
    status: str,
    *,
    id: str | None = None,
    tool_name: str | None = None,
    tool_args: dict | None = None,
) -> dict:
    event: dict = {"type": "step", "step": step, "status": status}
    if id is not None:
        event["id"] = id
    if tool_name is not None:
        event["toolName"] = tool_name
    if tool_args is not None:
        event["toolArgs"] = tool_args
    return event


def token(content: str) -> dict:
    return {"type": "token", "content": content}


def done(message_id: str) -> dict:
    return {"type": "done", "messageId": message_id}


def error(message: str) -> dict:
    return {"type": "error", "message": message}
