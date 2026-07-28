"""Detached, app-owned chat turns with a replay log for live-resume.

Each turn runs `run_turn` inside a background task (not the request's SSE
response), so a client disconnect/navigation cancels only the observing
response — the turn runs to completion and persists. Every event is appended to
an in-memory replay log; multiple observers (the original sender, plus a client
that navigates back) each tail the log from their own cursor. The registry is a
process singleton on app.state; the dict entry is the task's GC anchor.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.chat.loop import run_turn
from app.mcp.resolve import resolve_caller_mcp_tools
from app.tools.builtin.create_artifact import CreateArtifact
from app.tools.builtin.execute_code import ExecuteCode
from app.tools.builtin.search_kb import SearchKnowledgeBase
from app.tools.context import ToolContext
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class TurnInProgress(Exception):
    """Raised by spawn() when a room already has a live turn (→ HTTP 409)."""


def build_turn_registry() -> ToolRegistry:
    """A ToolRegistry with the three built-in tools (moved from chat/routes)."""
    registry = ToolRegistry()
    registry.register(SearchKnowledgeBase())
    registry.register(ExecuteCode())
    registry.register(CreateArtifact())
    return registry


class ChatTurnJob:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.events: list[dict] = []
        self.done = False
        self.task: asyncio.Task | None = None
        self.observers = 0  # count of live observe() streams (sender + resumers)
        self._wakeup = asyncio.Event()

    def _wake(self) -> None:
        # Wake current waiters and swap in a fresh event for the next round.
        self._wakeup.set()
        self._wakeup = asyncio.Event()

    def publish(self, event: dict) -> None:
        self.events.append(event)
        self._wake()

    def finish(self) -> None:
        self.done = True
        self._wake()


class ChatTurnRegistry:
    """Owns detached chat-turn tasks. Built once at startup with app singletons."""

    def __init__(self, *, model, client, embedder, session_factory, max_concurrent: int) -> None:
        self._jobs: dict[str, ChatTurnJob] = {}
        self._sem = asyncio.Semaphore(max_concurrent)
        self._model = model
        self._client = client
        self._embedder = embedder
        self._session_factory = session_factory

    def spawn(
        self, session_id: str, *, user_id: str, message: str,
        model_content: str, attachment_ids: list[str],
    ) -> ChatTurnJob:
        if session_id in self._jobs:
            raise TurnInProgress(session_id)
        job = ChatTurnJob(session_id)
        job.task = asyncio.create_task(
            self._run(job, user_id, message, model_content, attachment_ids)
        )
        self._jobs[session_id] = job
        job.task.add_done_callback(
            lambda _t, sid=session_id, this=job: (
                self._jobs.pop(sid, None) if self._jobs.get(sid) is this else None
            )
        )
        return job

    async def _run(
        self, job: ChatTurnJob, user_id: str, message: str,
        model_content: str, attachment_ids: list[str],
    ) -> None:
        from app.models import ChatSession, User

        try:
            async with self._sem:
                db = self._session_factory()
                try:
                    session = db.get(ChatSession, job.session_id)
                    user = db.get(User, user_id)
                    if session is None or user is None:
                        return
                    mcp_tools = await resolve_caller_mcp_tools(db, user)
                    registry = build_turn_registry()
                    for t in mcp_tools:
                        registry.register(t)
                    ctx = ToolContext(
                        user_id=user_id, session_id=job.session_id,
                        db=db, client=self._client, embedder=self._embedder,
                    )
                    async for ev in run_turn(
                        db=db, session=session, message=message,
                        model_content=model_content, attachment_ids=attachment_ids,
                        registry=registry, model=self._model, ctx=ctx,
                    ):
                        job.publish(ev)
                finally:
                    db.close()
        except Exception:
            # run_turn guards its own errors, but be safe: surface + terminate.
            logger.exception("chat turn failed for session %s", job.session_id)
            job.publish({"type": "error", "message": "Chat turn failed"})
        finally:
            job.finish()

    async def observe(self, session_id: str, from_index: int = 0) -> AsyncIterator[dict]:
        job = self._jobs.get(session_id)
        if job is None:  # no live turn (never started, or already finished+evicted)
            return
        job.observers += 1
        try:
            i = from_index
            while True:
                wakeup = job._wakeup      # capture BEFORE draining (avoids lost wakeups)
                while i < len(job.events):
                    yield job.events[i]
                    i += 1
                if job.done:
                    return
                await wakeup.wait()
        finally:
            job.observers -= 1

    def has_active(self, session_id: str) -> bool:
        return session_id in self._jobs

    def observer_count(self, session_id: str) -> int:
        job = self._jobs.get(session_id)
        return job.observers if job else 0

    def active_session_ids(self) -> set[str]:
        return set(self._jobs.keys())

    async def shutdown(self) -> None:
        tasks = [j.task for j in list(self._jobs.values()) if j.task is not None]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("chat turn errored during shutdown")
