"""Unit test: OpenAIModelClient surfaces reasoning_content as a reasoning chunk."""
from __future__ import annotations

import asyncio

from app.chat.client import ModelChunk, OpenAIModelClient


class _Delta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls or []


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, delta):
        self.choices = [_Choice(delta)]


async def _fake_response(chunks):
    for c in chunks:
        yield c


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kwargs):
        return _fake_response(self._chunks)


class _FakeChat:
    def __init__(self, chunks):
        self.completions = _FakeCompletions(chunks)


class _FakeAsyncClient:
    def __init__(self, chunks):
        self.chat = _FakeChat(chunks)


def test_stream_yields_reasoning_then_text():
    client = OpenAIModelClient()
    client._client = _FakeAsyncClient([
        _Chunk(_Delta(reasoning_content="thinking hard")),
        _Chunk(_Delta(content="the answer")),
    ])

    async def _run():
        return [c async for c in client.stream(
            messages=[{"role": "user", "content": "q"}], tools=[]
        )]

    chunks = asyncio.run(_run())
    assert chunks == [
        ModelChunk(type="reasoning", text="thinking hard"),
        ModelChunk(type="text", text="the answer"),
    ]
