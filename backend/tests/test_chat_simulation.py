"""Actual chat-turn registry/loop with an in-memory DB and a scripted model.

No TestClient lifespan, migrations, embeddings, or external model calls. These
tests prove tool wiring and delivery, not natural-language quality of a live LLM.
"""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.chat.client import ModelChunk
from app.chat.turns import ChatTurnRegistry, build_turn_registry
from app.db import Base
from app.models import ChatSession, Message, User

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "simulation" / "fleet.json"


@pytest.fixture
def simulation_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user = User(
            email="simulation@test.invalid", display_name="Simulation", password_hash="unused"
        )
        db.add(user)
        db.flush()
        session = ChatSession(user_id=user.id, title="New Chat")
        db.add(session)
        db.commit()
        identifiers = session.id, user.id
    yield factory, *identifiers
    engine.dispose()


def test_simulation_tool_registered_without_removing_existing_tools():
    names = [schema["function"]["name"] for schema in build_turn_registry().schemas()]
    assert set(names) == {
        "search_knowledge_base",
        "execute_code",
        "create_artifact",
        "run_simulation",
        "clarify_simulation",
        "resolve_simulation_clarification",
    }
    assert len(names) == len(set(names))


@pytest.mark.parametrize("kind", ["success", "invalid_input", "division_by_zero"])
def test_turn_delivers_simulation_result_to_model_and_persists_answer(simulation_session, kind):
    factory, session_id, user_id = simulation_session
    definition = json.loads(EXAMPLE.read_text("utf-8"))
    if kind == "invalid_input":
        del definition["parameters"][0]["origin"]
    elif kind == "division_by_zero":
        definition["outputs"][0]["expression"] = "fleet / 0"

    class ScriptedModel:
        def __init__(self):
            self.calls = []
            self.tool_response = None
            self.answer = None

        async def stream(self, messages, tools):
            self.calls.append(deepcopy(messages))
            if len(self.calls) == 1:
                assert "run_simulation" in [tool["function"]["name"] for tool in tools]
                yield ModelChunk(
                    type="tool_call",
                    id="simulation-call",
                    name="run_simulation",
                    arguments=definition,
                )
            else:
                tool_message = messages[-1]
                assert tool_message["role"] == "tool"
                assert tool_message["tool_call_id"] == "simulation-call"
                self.tool_response = json.loads(tool_message["content"])
                if kind == "success":
                    comparison = self.tool_response["result"]["scenarios"][0]["outputs"]["total"]
                    self.answer = f"Kapasitas skenario: {comparison['scenarioValue']} ton/hari."
                else:
                    self.answer = "Simulasi belum berhasil; input perlu diperbaiki."
                yield ModelChunk(type="text", text=self.answer)

    model = ScriptedModel()

    async def execute():
        registry = ChatTurnRegistry(
            model=model,
            client=None,
            embedder=None,
            session_factory=factory,
            max_concurrent=1,
        )
        job = registry.spawn(
            session_id,
            user_id=user_id,
            message="Simulasikan penambahan armada",
            model_content="Simulasikan penambahan armada",
            attachment_ids=[],
        )
        events = [event async for event in registry.observe(session_id)]
        await job.task
        await registry.shutdown()
        return events

    events = asyncio.run(execute())
    assert len(model.calls) == 2
    if kind == "success":
        assert model.tool_response["status"] == "success"
        assert model.tool_response["result"]["baseline"]["outputs"]["total"] == 50
        assert model.tool_response["result"]["scenarios"][0]["outputs"]["total"]["delta"] == 10
    else:
        assert model.tool_response["status"] == "error"
        assert model.tool_response["code"] == kind
        assert "result" not in model.tool_response
    assert any(event.get("toolName") == "run_simulation" for event in events)
    assert not any(event["type"] == "error" for event in events)
    assert events[-1]["type"] == "done"
    assert "".join(event["content"] for event in events if event["type"] == "token") == model.answer
    with factory() as db:
        messages = (
            db.query(Message).filter_by(session_id=session_id).order_by(Message.created_at).all()
        )
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[-1].content == model.answer
