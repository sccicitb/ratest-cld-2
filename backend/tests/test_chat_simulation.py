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
    streamed = "".join(event["content"] for event in events if event["type"] == "token")
    if kind == "success":
        assert streamed == model.answer
    else:
        assert "Belum ada hasil perhitungan baru" in streamed
        assert streamed != model.answer
    with factory() as db:
        messages = (
            db.query(Message).filter_by(session_id=session_id).order_by(Message.created_at).all()
        )
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[-1].content == streamed


def _single_definition():
    definition = json.loads(EXAMPLE.read_text())
    definition["scenarios"] = definition["scenarios"][:1]
    return definition


def _call_definition(definition, call_id="calculate"):
    return ModelChunk(type="tool_call", id=call_id, name="run_simulation", arguments=definition)


def _guard_turn(simulation_session, script, *, seed=False, registry=None):
    from app.chat.loop import run_turn
    from app.tools.builtin.run_simulation import RunSimulation
    from app.tools.context import ToolContext

    factory, session_id, user_id = simulation_session

    class Scripted:
        calls = 0

        async def stream(self, messages, tools):
            chunks = script[self.calls]
            self.calls += 1
            for chunk in chunks:
                yield chunk

    model = Scripted()

    async def execute():
        with factory() as db:
            session = db.get(ChatSession, session_id)
            ctx = ToolContext(
                user_id=user_id, session_id=session_id, db=db, client=None, embedder=None
            )
            if seed:
                await RunSimulation().execute(_single_definition(), ctx)
            before = deepcopy(session.simulation_state)
            events = [
                e
                async for e in run_turn(
                    db=db,
                    session=session,
                    message="Permintaan simulasi uji",
                    registry=registry or build_turn_registry(),
                    model=model,
                    ctx=ctx,
                )
            ]
            after = deepcopy(session.simulation_state)
            assert events[-1]["type"] == "done"
            saved = db.get(Message, events[-1]["messageId"]).content
            streamed = "".join(e.get("content", "") for e in events if e["type"] == "token")
            assert saved == streamed
            return events, saved, before, after

    return asyncio.run(execute())


@pytest.mark.parametrize("seed", [False, True])
def test_failed_calculation_cannot_stream_or_save_false_success(simulation_session, seed):
    events, saved, before, after = _guard_turn(
        simulation_session,
        [
            [_call_definition({})],
            [
                ModelChunk(type="reasoning", text="HIDDEN_MANUAL_999"),
                ModelChunk(type="text", text="Simulasi berhasil: HIDDEN_MANUAL_999 ton/hari"),
            ],
        ],
        seed=seed,
    )
    assert "HIDDEN_MANUAL_999" not in saved
    assert not any("HIDDEN_MANUAL_999" in e.get("content", "") for e in events)
    assert "Data simulasi belum sesuai format" in saved
    assert before == after
    if seed:
        assert "sebelumnya bukan hasil permintaan ini" in saved


def test_division_by_zero_has_server_explanation(simulation_session):
    invalid = _single_definition()
    invalid["outputs"][0]["expression"] = "fleet / 0"
    _, saved, before, after = _guard_turn(
        simulation_session,
        [
            [_call_definition(invalid)],
            [ModelChunk(type="text", text="HIDDEN_FALSE_RESULT")],
        ],
    )
    assert "pembagian dengan nol" in saved
    assert "HIDDEN_FALSE_RESULT" not in saved
    assert before == after


def test_later_successful_retry_releases_normal_answer(simulation_session):
    events, saved, _, after = _guard_turn(
        simulation_session,
        [
            [_call_definition({})],
            [
                ModelChunk(type="text", text="HIDDEN_RETRY_MATH"),
                _call_definition(_single_definition(), "retry"),
            ],
            [ModelChunk(type="text", text="Kapasitas hasil engine: 60 ton/hari.")],
        ],
    )
    assert saved == "Kapasitas hasil engine: 60 ton/hari."
    assert not any("HIDDEN_RETRY_MATH" in e.get("content", "") for e in events)
    assert after["result"]["scenarios"][0]["outputs"]["total"]["scenarioValue"] == 60


@pytest.mark.parametrize("success_first", [False, True])
def test_mixed_round_is_not_reported_as_full_success(simulation_session, success_first):
    calls = [_call_definition(_single_definition(), "good"), _call_definition({}, "bad")]
    if not success_first:
        calls.reverse()
    _, saved, _, after = _guard_turn(
        simulation_session,
        [
            calls,
            [ModelChunk(type="text", text="HIDDEN_ALL_SCENARIOS_SUCCEEDED")],
        ],
    )
    assert "HIDDEN_ALL_SCENARIOS_SUCCEEDED" not in saved
    assert "Ada perhitungan pada pesan ini yang berhasil dan sudah tersimpan" in saved
    assert after["result"]["scenarios"][0]["outputs"]["total"]["scenarioValue"] == 60


def test_unrelated_tool_success_does_not_clear_simulation_failure(simulation_session):
    from app.tools.builtin.run_simulation import RunSimulation
    from app.tools.registry import ToolRegistry

    class OtherTool:
        name = "other"

        def schema(self):
            return {
                "type": "function",
                "function": {"name": self.name, "parameters": {"type": "object"}},
            }

        async def execute(self, args, ctx):
            return '{"status":"success","result":{}}'

    registry = ToolRegistry()
    registry.register(RunSimulation())
    registry.register(OtherTool())
    _, saved, before, after = _guard_turn(
        simulation_session,
        [
            [_call_definition({})],
            [ModelChunk(type="tool_call", id="other", name="other", arguments={})],
            [ModelChunk(type="text", text="HIDDEN_UNRELATED_SUCCESS")],
        ],
        registry=registry,
    )
    assert "HIDDEN_UNRELATED_SUCCESS" not in saved
    assert "Belum ada hasil perhitungan baru" in saved
    assert before == after


def test_iteration_cap_cannot_force_unverified_success(simulation_session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_tool_iterations", 1)
    _, saved, before, after = _guard_turn(
        simulation_session,
        [
            [_call_definition({})],
            [
                ModelChunk(type="text", text="HIDDEN_FORCED_SUCCESS"),
                _call_definition(_single_definition(), "ignored"),
            ],
        ],
    )
    assert "HIDDEN_FORCED_SUCCESS" not in saved
    assert "Belum ada hasil perhitungan baru" in saved
    assert before == after


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        "{}",
        '{"status":{}}',
        '{"status":"success"}',
        '{"status":"success","result":{}}',
    ],
)
def test_malformed_simulation_result_is_not_a_success(simulation_session, raw):
    from app.tools.registry import ToolRegistry

    class BrokenSimulation:
        name = "run_simulation"

        def schema(self):
            return {
                "type": "function",
                "function": {"name": self.name, "parameters": {"type": "object"}},
            }

        async def execute(self, args, ctx):
            return raw

    registry = ToolRegistry()
    registry.register(BrokenSimulation())
    _, saved, before, after = _guard_turn(
        simulation_session,
        [
            [_call_definition({})],
            [ModelChunk(type="text", text="HIDDEN_MALFORMED_SUCCESS")],
        ],
        registry=registry,
    )
    assert "HIDDEN_MALFORMED_SUCCESS" not in saved
    assert "Belum ada hasil perhitungan baru" in saved
    assert before == after


def test_unexpected_simulation_tool_exception_is_guarded(simulation_session, monkeypatch):
    from app.tools.builtin.run_simulation import RunSimulation

    async def broken(self, args, ctx):
        raise RuntimeError("INTERNAL_DETAIL_NOT_FOR_USER")

    monkeypatch.setattr(RunSimulation, "execute", broken)
    _, saved, _, _ = _guard_turn(
        simulation_session,
        [
            [_call_definition({})],
            [ModelChunk(type="text", text="HIDDEN_EXCEPTION_SUCCESS")],
        ],
    )
    assert "INTERNAL_DETAIL_NOT_FOR_USER" not in saved
    assert "HIDDEN_EXCEPTION_SUCCESS" not in saved
    assert "Belum ada hasil perhitungan baru" in saved


def test_success_then_later_failure_stays_partial(simulation_session):
    _, saved, _, after = _guard_turn(
        simulation_session,
        [
            [_call_definition(_single_definition(), "first")],
            [_call_definition({}, "second")],
            [ModelChunk(type="text", text="HIDDEN_LATER_FAILURE")],
        ],
    )
    assert "HIDDEN_LATER_FAILURE" not in saved
    assert "tidak berarti seluruh permintaan berhasil" in saved
    assert after["result"]["scenarios"][0]["outputs"]["total"]["scenarioValue"] == 60


def test_success_after_mixed_round_recovers(simulation_session):
    corrected = _single_definition()
    corrected["scenarios"][0]["overrides"]["fleet"]["value"] = 14
    _, saved, _, after = _guard_turn(
        simulation_session,
        [
            [_call_definition(_single_definition(), "good"), _call_definition({}, "bad")],
            [_call_definition(corrected, "corrected")],
            [ModelChunk(type="text", text="Hasil engine setelah koreksi: 70 ton/hari.")],
        ],
    )
    assert saved == "Hasil engine setelah koreksi: 70 ton/hari."
    assert after["result"]["scenarios"][0]["outputs"]["total"]["scenarioValue"] == 70


def test_failed_calculation_can_recover_with_server_clarification(simulation_session):
    events, saved, before, after = _guard_turn(
        simulation_session,
        [
            [_call_definition({})],
            [
                ModelChunk(
                    type="tool_call",
                    id="clarify",
                    name="clarify_simulation",
                    arguments={"parameterId": "fleet", "amount": 5},
                )
            ],
        ],
        seed=True,
    )
    assert "dihitung dari mana" in saved
    assert after["pending"]["kind"] == "add"
    assert after["result"] == before["result"]
    assert events[-1]["type"] == "done"
