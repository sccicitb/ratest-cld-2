"""The system prompt (§7) — who the assistant is and what language it answers in.

Its own module rather than a constant in `loop.py`: this is content, edited on
its own cadence by whoever tunes the deployment, and `loop.py` is already the
largest file on the chat path.
"""
from __future__ import annotations

from app.config import settings

#: Overridden by SYSTEM_PROMPT. Indonesian on purpose — the instruction is more
#: reliably followed when it is written in the language it is asking for.
DEFAULT_SYSTEM_PROMPT = (
    "Kamu adalah Citya, asisten kota. "
    "Selalu jawab dalam Bahasa Indonesia, apa pun bahasa yang digunakan pengguna. "
    "Jika pengguna bertanya siapa kamu, perkenalkan diri sebagai Citya, asisten kota."
)


def system_message() -> dict | None:
    """The system message to prepend to a turn, or None when disabled.

    Read per call rather than captured at import: tests monkeypatch the setting,
    and an operator changing .env gets the new prompt on restart with no other
    moving parts.
    """
    text = DEFAULT_SYSTEM_PROMPT if settings.system_prompt is None else settings.system_prompt
    if not text:
        return None
    return {"role": "system", "content": text}
