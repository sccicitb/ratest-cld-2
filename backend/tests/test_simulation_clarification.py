"""Persistent clarification, ownership, engine arithmetic and chat transport."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chat.client import ModelChunk
from app.chat.loop import run_turn
from app.chat.turns import build_turn_registry
from app.db import Base
from app.models import ChatSession, Message, User
from app.simulation.conversation import (
    ConversationError,
    request_clarification,
    resolve_clarification,
)
from app.tools.builtin.run_simulation import RunSimulation
from app.tools.context import ToolContext

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/simulation/fleet.json"


@pytest.fixture
def context():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(
            User(
                id="owner", email="owner@test.invalid", display_name="Owner", password_hash="unused"
            )
        )
        db.flush()
        db.add(ChatSession(id="session", user_id="owner", title="Test"))
        db.commit()
        ctx = ToolContext(user_id="owner", session_id="session", db=db, client=None, embedder=None)
        definition = json.loads(EXAMPLE.read_text())
        # Existing tests exercise an unambiguous single scenario (15 fleet).
        definition["scenarios"] = [definition["scenarios"][-1]]
        result = json.loads(asyncio.run(RunSimulation().execute(definition, ctx)))
        assert result["status"] == "success"
        yield ctx, factory
    engine.dispose()


@pytest.mark.parametrize("reference,expected", [("baseline", 15), ("last_scenario", 20)])
def test_anchored_options_and_resolution(context, reference, expected):
    ctx, factory = context
    pending = request_clarification(ctx, "fleet", 5)
    assert [o["targetValue"] for o in pending["options"]] == [15, 20]
    with factory() as reopened:
        assert reopened.get(ChatSession, ctx.session_id).simulation_state["pending"] == pending
    response = resolve_clarification(ctx, pending["id"], reference)
    assert response["result"]["scenarios"][0]["parameters"]["fleet"]["value"] == expected
    assert response["result"]["baseline"]["parameters"]["fleet"]["value"] == 10
    assert ctx.db.get(ChatSession, ctx.session_id).simulation_state["pending"] is None
    assert f"{expected * 5:g}" in ctx.simulation_reply
    with pytest.raises(ConversationError, match="tidak aktif"):
        resolve_clarification(ctx, pending["id"], reference)


def test_natural_answer_can_change_increment(context):
    ctx, _ = context
    pending = request_clarification(ctx, "fleet", 5)
    response = resolve_clarification(ctx, pending["id"], "last_scenario", amount=3)
    assert response["result"]["scenarios"][0]["outputs"]["total"]["scenarioValue"] == 90
    assert "15 + 3" in ctx.simulation_reply


def test_reference_preserves_other_parameters(context):
    ctx, _ = context
    definition = json.loads(EXAMPLE.read_text())
    definition["scenarios"][-1]["overrides"]["capacity"] = {
        "value": 8,
        "origin": {"kind": "user_input", "description": "capacity change"},
    }
    definition["scenarios"] = [definition["scenarios"][-1]]
    # Determine actual capacity parameter identifier from example.
    capacity = definition["parameters"][1]["id"]
    definition["scenarios"][-1]["overrides"][capacity] = definition["scenarios"][-1][
        "overrides"
    ].pop("capacity")
    assert json.loads(asyncio.run(RunSimulation().execute(definition, ctx)))["status"] == "success"
    pending = request_clarification(ctx, "fleet", 5)
    result = resolve_clarification(ctx, pending["id"], "last_scenario")["result"]
    assert result["scenarios"][0]["outputs"]["total"]["scenarioValue"] == 160
    assert result["baseline"]["outputs"]["total"] == 50


def test_pending_blocks_overwrite_and_can_be_cancelled(context):
    ctx, _ = context
    pending = request_clarification(ctx, "fleet", 5)
    original = deepcopy(ctx.db.get(ChatSession, ctx.session_id).simulation_state)
    response = json.loads(
        asyncio.run(RunSimulation().execute(json.loads(EXAMPLE.read_text()), ctx))
    )
    assert response["code"] == "clarification_pending"
    with pytest.raises(ConversationError):
        request_clarification(ctx, "fleet", 3)
    with pytest.raises(ConversationError):
        resolve_clarification(ctx, "stale", "baseline")
    with pytest.raises(ConversationError):
        resolve_clarification(ctx, pending["id"], None)
    assert ctx.db.get(ChatSession, ctx.session_id).simulation_state == original
    resolve_clarification(ctx, pending["id"], cancel=True)
    state = ctx.db.get(ChatSession, ctx.session_id).simulation_state
    assert state["result"] == original["result"]
    assert state["pending"] is None


def test_ownership_and_parameter_validation(context):
    ctx, _ = context
    with pytest.raises(ConversationError, match="Parameter"):
        request_clarification(ctx, "unknown", 5)
    ctx.user_id = "attacker"
    with pytest.raises(ConversationError, match="tidak ditemukan"):
        request_clarification(ctx, "fleet", 5)
    response = json.loads(
        asyncio.run(RunSimulation().execute(json.loads(EXAMPLE.read_text()), ctx))
    )
    assert response["code"] == "session_required"


def test_invalid_engine_operation_does_not_consume_question(context):
    ctx, _ = context
    pending = request_clarification(ctx, "fleet", 5)
    before = deepcopy(ctx.db.get(ChatSession, ctx.session_id).simulation_state)
    with pytest.raises(ValueError):
        resolve_clarification(ctx, pending["id"], "baseline", amount=float("inf"))
    assert ctx.db.get(ChatSession, ctx.session_id).simulation_state == before


def test_clicked_selection_bypasses_model_and_is_persisted(context):
    ctx, _ = context
    pending = request_clarification(ctx, "fleet", 5)
    ctx.simulation_reply = None
    ctx.simulation_selection = {"questionId": pending["id"], "reference": "baseline"}

    class NoModel:
        async def stream(self, messages, tools):
            raise AssertionError("Clicked options must not invoke model")
            yield

    async def consume():
        return [
            e
            async for e in run_turn(
                db=ctx.db,
                session=ctx.db.get(ChatSession, ctx.session_id),
                message="Dari baseline",
                registry=build_turn_registry(),
                model=NoModel(),
                ctx=ctx,
            )
        ]

    events = asyncio.run(consume())
    assert events[-1]["type"] == "done"
    assert "10 + 5" in events[0]["content"]
    saved = ctx.db.get(Message, events[-1]["messageId"])
    assert saved.content == events[0]["content"]


def test_natural_reply_resolves_through_tool_and_server_renders_result(context):
    ctx, _ = context
    pending = request_clarification(ctx, "fleet", 5)
    ctx.simulation_reply = None

    class InterpretingModel:
        async def stream(self, messages, tools):
            assert any(pending["id"] in str(m["content"]) for m in messages)
            yield ModelChunk(
                type="tool_call",
                id="resolve",
                name="resolve_simulation_clarification",
                arguments={"questionId": pending["id"], "reference": "last_scenario", "amount": 3},
            )

    async def consume():
        return [
            e
            async for e in run_turn(
                db=ctx.db,
                session=ctx.db.get(ChatSession, ctx.session_id),
                message="Dari terakhir tapi tambah tiga",
                registry=build_turn_registry(),
                model=InterpretingModel(),
                ctx=ctx,
            )
        ]

    events = asyncio.run(consume())
    assert events[-1]["type"] == "done"
    assert "15 + 3" in "".join(e.get("content", "") for e in events if e["type"] == "token")


def test_route_options_reload_click_and_stale_choice(
    client, auth_headers, session_factory, demo_user
):
    session_id = client.post("/api/sessions", headers=auth_headers).json()["id"]
    with session_factory() as db:
        ctx = ToolContext(
            user_id=demo_user["id"], session_id=session_id, db=db, client=None, embedder=None
        )
        definition = json.loads(EXAMPLE.read_text())
        definition["scenarios"] = [definition["scenarios"][-1]]
        assert (
            json.loads(asyncio.run(RunSimulation().execute(definition, ctx)))["status"] == "success"
        )
        pending = request_clarification(ctx, "fleet", 5)
    url = f"/api/sessions/{session_id}"
    assert client.get(url + "/simulation", headers=auth_headers).json()["pending"] == pending
    payload = {
        "message": "untrusted label ignored",
        "simulationSelection": {"questionId": pending["id"], "optionId": "last_scenario"},
    }
    response = client.post(url + "/chat", headers=auth_headers, json=payload)
    assert response.status_code == 200
    assert '"type": "done"' in response.text or '"type":"done"' in response.text
    assert client.get(url + "/simulation", headers=auth_headers).json()["pending"] is None
    messages = client.get(url + "/messages", headers=auth_headers).json()
    assert "untrusted label" not in messages[-2]["content"]
    assert "15 + 5" in messages[-1]["content"]
    assert client.post(url + "/chat", headers=auth_headers, json=payload).status_code == 409


def test_state_endpoint_enforces_ownership(client, auth_headers, session_factory):
    with session_factory() as db:
        db.add(
            User(
                id="other", email="other@test.invalid", display_name="Other", password_hash="unused"
            )
        )
        db.flush()
        db.add(ChatSession(id="other-session", user_id="other"))
        db.commit()
    assert (
        client.get("/api/sessions/other-session/simulation", headers=auth_headers).status_code
        == 404
    )
    assert client.get("/api/sessions/other-session/simulation").status_code == 401


@pytest.fixture
def client(session_factory):
    """Exercise auth/routes/SSE without startup downloads or real external services."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    def get_test_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = get_test_db
    # Not entering the context deliberately skips the production lifespan.
    test_client = TestClient(app)
    yield test_client
    test_client.close()
    app.dependency_overrides.clear()


@pytest.mark.parametrize("tool_name", ["clarify_simulation", "resolve_simulation_clarification"])
def test_invalid_clarification_fields_are_not_echoed(context, tool_name):
    ctx, _ = context
    response = asyncio.run(
        build_turn_registry().execute(tool_name, {"unexpected": "do-not-echo-this"}, ctx)
    )
    assert json.loads(response)["status"] == "error"
    assert "do-not-echo-this" not in response


def save_multiple(ctx):
    definition = json.loads(EXAMPLE.read_text())
    definition["scenarios"] = [
        {
            "id": "target",
            "label": "Target 4 armada",
            "overrides": {
                "fleet": {
                    "value": 4,
                    "origin": {"kind": "user_input", "description": "Requested target"},
                },
                "capacity": {
                    "value": 6,
                    "origin": {"kind": "user_input", "description": "Chosen capacity"},
                },
            },
        },
        {
            "id": "comparison",
            "label": "Pembanding 17 armada",
            "overrides": {
                "fleet": {
                    "value": 17,
                    "origin": {"kind": "synthetic", "description": "Comparison only"},
                },
            },
        },
    ]
    result = json.loads(asyncio.run(RunSimulation().execute(definition, ctx)))
    assert result["status"] == "success"
    pending = ctx.db.get(ChatSession, ctx.session_id).simulation_state["pending"]
    assert pending["kind"] == "select_scenario"
    resolve_clarification(ctx, pending["id"], cancel=True)
    ctx.simulation_reply = None
    return definition


def test_multiple_alternatives_have_no_implicit_active_identity(context):
    from app.simulation.conversation import active_scenario

    ctx, _ = context
    save_multiple(ctx)
    state = ctx.db.get(ChatSession, ctx.session_id).simulation_state
    assert active_scenario(state) is None
    assert state["activeScenarioId"] is None
    question = request_clarification(ctx, "fleet", 5)
    assert [o["id"] for o in question["options"]] == [
        "baseline",
        "scenario:target",
        "scenario:comparison",
    ]
    assert [o["targetValue"] for o in question["options"]] == [15, 9, 22]
    # There is no last_scenario alias until an identity is selected.
    with pytest.raises(ConversationError):
        resolve_clarification(ctx, question["id"], "last_scenario")


def test_selected_identity_survives_reordering_reload_and_addition(context):
    ctx, factory = context
    save_multiple(ctx)
    pending = request_clarification(ctx)
    result = resolve_clarification(ctx, pending["id"], "scenario:target")
    assert result == {"status": "selected", "activeScenarioId": "target"}
    room = ctx.db.get(ChatSession, ctx.session_id)
    state = deepcopy(room.simulation_state)
    original_result = deepcopy(state["result"])
    # Selecting an identity leaves the baseline and all results unchanged.
    assert state["result"]["baseline"]["outputs"]["total"] == 50
    state["result"]["scenarios"].reverse()
    room.simulation_state = state
    ctx.db.commit()
    with factory() as reopened:
        reloaded_ctx = ToolContext(
            user_id=ctx.user_id, session_id=ctx.session_id, db=reopened, client=None, embedder=None
        )
        pending = request_clarification(reloaded_ctx, "fleet", 5)
        assert pending["options"][1]["scenarioId"] == "target"
        assert pending["options"][1]["baseValue"] == 4
        assert pending["options"][1]["targetValue"] == 9
        # Change order while pending: the anchored ID still resolves to target.
        current = reopened.get(ChatSession, ctx.session_id)
        current.simulation_state = {**current.simulation_state, "result": original_result}
        reopened.commit()
        result = resolve_clarification(reloaded_ctx, pending["id"], "last_scenario")["result"]
        assert result["scenarios"][0]["parameters"]["fleet"]["value"] == 9
        assert result["scenarios"][0]["parameters"]["capacity"]["value"] == 6
        assert result["scenarios"][0]["outputs"]["total"]["scenarioValue"] == 54
        assert result["baseline"]["outputs"]["total"] == 50
        assert current.simulation_state["activeScenarioId"] == "clarified_scenario"


def test_legacy_multiple_snapshot_does_not_infer_last(context):
    from app.simulation.conversation import active_scenario

    ctx, _ = context
    save_multiple(ctx)
    room = ctx.db.get(ChatSession, ctx.session_id)
    state = deepcopy(room.simulation_state)
    state.pop("activeScenarioId")
    room.simulation_state = state
    ctx.db.commit()
    assert active_scenario(state) is None
    question = request_clarification(ctx)
    assert len(question["options"]) == 2
    before = deepcopy(room.simulation_state["result"])
    with pytest.raises(ConversationError):
        resolve_clarification(ctx, question["id"], "scenario:target", amount=3)
    with pytest.raises(ConversationError):
        resolve_clarification(ctx, question["id"], "scenario:foreign")
    resolve_clarification(ctx, question["id"], "scenario:target")
    assert room.simulation_state["result"] == before


def test_legacy_pending_requests_reconfirmation_without_calculation(context):
    ctx, _ = context
    save_multiple(ctx)
    room = ctx.db.get(ChatSession, ctx.session_id)
    before = deepcopy(room.simulation_state["result"])
    legacy = {
        "id": "old-question",
        "revision": room.simulation_state["revision"],
        "parameterId": "fleet",
        "amount": 5,
        "question": "Old question",
        "options": [
            {"id": "last_scenario", "baseValue": 17, "targetValue": 22, "label": "Old last"}
        ],
    }
    room.simulation_state = {**room.simulation_state, "pending": legacy}
    ctx.db.commit()
    result = resolve_clarification(ctx, "old-question", "last_scenario")
    assert result["status"] == "needs_clarification"
    assert result["pending"]["id"] != "old-question"
    assert len(result["pending"]["options"]) == 3
    assert room.simulation_state["result"] == before
    with pytest.raises(ConversationError):
        resolve_clarification(ctx, "old-question", "last_scenario")


def test_sole_legacy_scenario_is_unambiguous(context):
    from app.simulation.conversation import active_scenario

    ctx, _ = context
    room = ctx.db.get(ChatSession, ctx.session_id)
    state = deepcopy(room.simulation_state)
    state.pop("activeScenarioId")
    room.simulation_state = state
    ctx.db.commit()
    assert active_scenario(state)["id"] == "add_five"
    question = request_clarification(ctx, "fleet", 5)
    assert question["options"][1]["scenarioId"] == "add_five"


def test_natural_ordinal_uses_actual_option_id(context):
    ctx, _ = context
    save_multiple(ctx)
    pending = request_clarification(ctx)
    ctx.simulation_reply = None

    class ChoosingModel:
        async def stream(self, messages, tools):
            yield ModelChunk(
                type="tool_call",
                id="choose",
                name="resolve_simulation_clarification",
                arguments={"questionId": pending["id"], "reference": "scenario:target"},
            )

    async def consume():
        return [
            e
            async for e in run_turn(
                db=ctx.db,
                session=ctx.db.get(ChatSession, ctx.session_id),
                message="Pilih yang pertama",
                registry=build_turn_registry(),
                model=ChoosingModel(),
                ctx=ctx,
            )
        ]

    events = asyncio.run(consume())
    assert events[-1]["type"] == "done"
    assert ctx.db.get(ChatSession, ctx.session_id).simulation_state["activeScenarioId"] == "target"


def test_multi_result_creates_clickable_selection_and_endpoint_persists_choice(
    client, auth_headers, session_factory, demo_user
):
    session_id = client.post("/api/sessions", headers=auth_headers).json()["id"]
    definition = json.loads(EXAMPLE.read_text())
    with session_factory() as db:
        ctx = ToolContext(
            user_id=demo_user["id"], session_id=session_id, db=db, client=None, embedder=None
        )
        response = json.loads(asyncio.run(RunSimulation().execute(definition, ctx)))
        assert response["requiresScenarioSelection"] is True
    url = f"/api/sessions/{session_id}"
    state = client.get(url + "/simulation", headers=auth_headers).json()
    assert state["activeScenarioId"] is None
    assert state["pending"]["kind"] == "select_scenario"
    assert [o["id"] for o in state["pending"]["options"]] == [
        "scenario:add_two",
        "scenario:add_five",
    ]
    body = {
        "message": "ignored label",
        "simulationSelection": {
            "questionId": state["pending"]["id"],
            "optionId": "scenario:add_two",
        },
    }
    assert client.post(url + "/chat", headers=auth_headers, json=body).status_code == 200
    state = client.get(url + "/simulation", headers=auth_headers).json()
    assert state["activeScenarioId"] == "add_two"
    assert state["activeScenarioLabel"] == "Tambah dua armada"
    assert state["pending"] is None
    assert client.post(url + "/chat", headers=auth_headers, json=body).status_code == 409
    with session_factory() as db:
        ctx = ToolContext(
            user_id=demo_user["id"], session_id=session_id, db=db, client=None, embedder=None
        )
        pending = request_clarification(ctx, "fleet", 5)
        assert pending["options"][1]["baseValue"] == 12
        result = resolve_clarification(ctx, pending["id"], "last_scenario")["result"]
        assert result["scenarios"][0]["outputs"]["total"]["scenarioValue"] == 85
        assert result["baseline"]["outputs"]["total"] == 50


def test_model_cannot_select_new_question_in_same_turn(context):
    ctx, _ = context
    definition = json.loads(EXAMPLE.read_text())

    class SelfSelectingModel:
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield ModelChunk(
                    type="tool_call", id="run", name="run_simulation", arguments=definition
                )
            elif self.calls == 2:
                state = ctx.db.get(ChatSession, ctx.session_id).simulation_state
                yield ModelChunk(
                    type="tool_call",
                    id="choose",
                    name="resolve_simulation_clarification",
                    arguments={
                        "questionId": state["pending"]["id"],
                        "reference": "scenario:add_five",
                    },
                )
            else:
                assert "Wait for the user" in messages[-1]["content"]
                yield ModelChunk(type="text", text="Silakan pilih acuan melalui tombol.")

    async def consume():
        return [
            e
            async for e in run_turn(
                db=ctx.db,
                session=ctx.db.get(ChatSession, ctx.session_id),
                message="Bandingkan dua alternatif",
                registry=build_turn_registry(),
                model=SelfSelectingModel(),
                ctx=ctx,
            )
        ]

    events = asyncio.run(consume())
    assert events[-1]["type"] == "done"
    state = ctx.db.get(ChatSession, ctx.session_id).simulation_state
    assert state["activeScenarioId"] is None
    assert state["pending"]["kind"] == "select_scenario"


def test_legacy_single_pending_keeps_working(context):
    ctx, _ = context
    room = ctx.db.get(ChatSession, ctx.session_id)
    pending = request_clarification(ctx, "fleet", 5)
    state = deepcopy(room.simulation_state)
    state.pop("activeScenarioId")
    for option in state["pending"]["options"]:
        option.pop("scenarioId")
    room.simulation_state = state
    ctx.db.commit()
    result = resolve_clarification(ctx, pending["id"], "last_scenario")["result"]
    assert result["scenarios"][0]["parameters"]["fleet"]["value"] == 20


def test_failed_legacy_reconfirmation_keeps_original_pending(context):
    ctx, _ = context
    save_multiple(ctx)
    room = ctx.db.get(ChatSession, ctx.session_id)
    legacy = {
        "id": "old",
        "revision": room.simulation_state["revision"],
        "parameterId": "fleet",
        "amount": 1000,
        "options": [{"id": "last_scenario", "baseValue": 17, "label": "Old"}],
    }
    room.simulation_state = {**room.simulation_state, "pending": legacy}
    ctx.db.commit()
    before = deepcopy(room.simulation_state)
    with pytest.raises(ValueError):
        resolve_clarification(ctx, "old", "last_scenario")
    ctx.db.commit()
    assert room.simulation_state == before
