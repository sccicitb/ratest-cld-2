"""Simulation adapter tests, independent of app startup and live model services."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ChatSession, User
from app.tools.builtin.run_simulation import MAX_INPUT_BYTES, RunSimulation
from app.tools.context import ToolContext

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "simulation" / "fleet.json"


@pytest.fixture
def definition():
    return json.loads(EXAMPLE.read_text("utf-8"))


def call(args, session_id="owned-session"):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            User(
                id="owned-user",
                email="test@example.invalid",
                display_name="Test",
                password_hash="unused",
            )
        )
        db.flush()
        db.add(ChatSession(id="owned-session", user_id="owned-user"))
        db.commit()
        ctx = ToolContext(
            user_id="owned-user", session_id=session_id, db=db, client=None, embedder=None
        )
        result = json.loads(asyncio.run(RunSimulation().execute(args, ctx)))
    engine.dispose()
    return result


def test_success_contains_calculated_results_units_origins_and_assumptions(definition):
    original = deepcopy(definition)
    response = call(definition)
    assert response["status"] == "success"
    result = response["result"]
    assert result["baseline"]["outputs"]["total"] == 50
    assert result["scenarios"][0]["outputs"]["total"]["percentageChange"] == 20
    assert result["definition"]["outputs"][0]["unit"] == "ton/hari"
    assert result["baseline"]["parameters"]["fleet"]["origin"]["kind"] == "synthetic"
    assert result["definition"]["assumptions"] == definition["assumptions"]
    assert definition == original


def test_requires_session(definition):
    assert call(definition, session_id=None)["code"] == "session_required"


@pytest.mark.parametrize("field", ["user_id", "sessionId", "scope"])
def test_cannot_supply_server_scope_in_arguments(definition, field):
    definition[field] = "other-user-or-session"
    assert call(definition)["code"] == "invalid_input"


@pytest.mark.parametrize("args", [[], "text", None, {"parameters": float("inf")}])
def test_rejects_non_object_or_non_finite_arguments(args):
    assert call(args)["code"] == "invalid_input"


def test_validation_errors_explain_location_without_echoing_inputs(definition):
    definition["parameters"][0]["origin"] = {
        "kind": "referenced_data",
        "description": "Unverified report",
    }
    response = call(definition)
    assert response["code"] == "invalid_input"
    issue = response["issues"][0]
    assert issue["field"] == "parameters.0.origin"
    assert "source reference" in issue["message"]
    assert "input" not in issue


def test_utf8_payload_limit_is_enforced_in_bytes(definition):
    definition["title"] = "é" * MAX_INPUT_BYTES
    assert call(definition)["code"] == "input_limit"


@pytest.mark.parametrize(
    "collection,maximum", [("parameters", 32), ("outputs", 8), ("scenarios", 8)]
)
def test_collection_limits(definition, collection, maximum):
    prototype = definition[collection][0]
    definition[collection] = [dict(deepcopy(prototype), id=f"item_{i}") for i in range(maximum + 1)]
    assert call(definition)["code"] == "input_limit"


@pytest.mark.parametrize("wrapper", [None, "definition", "arguments"])
def test_result_limit_returns_error_without_truncating_json(definition, wrapper):
    definition["parameters"][1]["origin"]["description"] = "x" * 16000
    scenario = definition["scenarios"][0]
    definition["scenarios"] = [dict(deepcopy(scenario), id=f"scenario_{i}") for i in range(8)]
    assert len(json.dumps(definition).encode()) < MAX_INPUT_BYTES
    response = call({wrapper: definition} if wrapper else definition)
    assert response["code"] == "result_limit"
    assert "result" not in response


@pytest.mark.parametrize(
    "expression,code",
    [
        ("fleet / 0", "division_by_zero"),
        ("unknown * capacity", "unknown_parameter"),
        ("fleet ** 2", "unsupported_expression"),
    ],
)
def test_engine_errors_are_structured_and_not_success(definition, expression, code):
    definition["outputs"][0]["expression"] = expression
    response = call(definition)
    assert response["status"] == "error"
    assert response["code"] == code
    assert response["outputId"] == "total"
    assert "result" not in response


def test_scenario_calculation_error_names_scenario(definition):
    definition["outputs"][0]["expression"] = "capacity / fleet"
    definition["scenarios"][1]["overrides"]["fleet"]["value"] = 0
    assert call(definition)["scenarioId"] == "add_five"


def test_schema_is_complete_and_resolvable_and_has_no_scope_arguments():
    from jsonschema import Draft202012Validator

    schema = RunSimulation().schema()["function"]
    assert schema["name"] == "run_simulation"
    parameters = schema["parameters"]
    Draft202012Validator.check_schema(parameters)
    Draft202012Validator(parameters).validate(json.loads(EXAMPLE.read_text("utf-8")))
    assert "baselineValue" in parameters["$defs"]["SimulationParameter"]["properties"]
    assert "userId" not in parameters["properties"]
    assert "sessionId" not in parameters["properties"]
    assert parameters["properties"]["scenarios"]["maxItems"] == 8


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
def test_supported_envelope_matches_standard_results_without_mutation(definition, wrapper):
    original = deepcopy(definition)
    wrapped = {wrapper: definition}
    assert call(wrapped) == call(original)
    assert wrapped == {wrapper: original}


@pytest.mark.parametrize("outer", ["definition", "arguments"])
@pytest.mark.parametrize("inner", ["definition", "arguments"])
def test_nested_envelopes_are_rejected(definition, outer, inner):
    response = call({outer: {inner: definition}})
    assert response["code"] == "invalid_input"
    assert "Nested" in response["message"]


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
@pytest.mark.parametrize("extra", ["title", "scope", "userId", "sessionId", "unknown"])
def test_envelope_siblings_are_never_silently_dropped(definition, wrapper, extra):
    response = call({wrapper: definition, extra: "DO_NOT_ECHO"})
    assert response["code"] == "invalid_input"
    assert "only top-level field" in response["message"]
    assert "DO_NOT_ECHO" not in json.dumps(response)


def test_both_envelopes_are_ambiguous_even_if_identical(definition):
    assert call({"definition": definition, "arguments": definition})["code"] == "invalid_input"


@pytest.mark.parametrize("value", [None, [], "encoded definition", 20, True])
@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
def test_envelope_contents_must_be_an_object(wrapper, value):
    assert call({wrapper: value})["code"] == "invalid_input"


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
@pytest.mark.parametrize("field", ["scope", "userId", "sessionId"])
def test_scoping_fields_inside_envelope_are_still_rejected(definition, wrapper, field):
    definition[field] = "other-user"
    assert call({wrapper: definition})["code"] == "invalid_input"


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
@pytest.mark.parametrize("value", ["20", True, None])
def test_values_are_not_coerced(definition, wrapper, value):
    definition["parameters"][0]["baselineValue"] = value
    response = call({wrapper: definition})
    assert response["code"] == "invalid_input"
    assert response["issues"][0]["field"] == "parameters.0.baselineValue"
    assert "finite numbers, not strings" in response["hint"]


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
def test_arrays_are_not_repaired_or_decoded(definition, wrapper):
    definition["scenarios"] = json.dumps(definition["scenarios"])
    response = call({wrapper: definition})
    assert response["code"] == "invalid_input"
    assert response["issues"][0]["field"] == "scenarios"
    assert response["issues"][0]["type"] == "list_type"
    assert "arrays of objects" in response["hint"]


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
def test_envelope_bytes_count_toward_original_payload_limit(definition, wrapper):
    # ASCII lets the measured encoded size equal the UTF-8 byte count precisely.
    definition["title"] = "x"
    base_size = len(json.dumps(definition, ensure_ascii=False).encode())
    definition["title"] += "x" * (MAX_INPUT_BYTES - base_size)
    assert len(json.dumps(definition, ensure_ascii=False).encode()) == MAX_INPUT_BYTES
    assert call(definition)["status"] == "success"
    assert call({wrapper: definition})["code"] == "input_limit"


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
@pytest.mark.parametrize("field,limit", [("parameters", 32), ("outputs", 8), ("scenarios", 8)])
def test_envelope_collections_keep_existing_limits(definition, wrapper, field, limit):
    prototype = definition[field][0]
    definition[field] = [dict(deepcopy(prototype), id=f"item_{i}") for i in range(limit + 1)]
    assert call({wrapper: definition})["code"] == "input_limit"


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
@pytest.mark.parametrize(
    "expression,code", [("fleet / 0", "division_by_zero"), ("fleet ** 2", "unsupported_expression")]
)
def test_envelope_keeps_engine_restrictions(definition, wrapper, expression, code):
    definition["outputs"][0]["expression"] = expression
    assert call({wrapper: definition})["code"] == code


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
def test_unknown_envelope_is_not_accepted(definition, wrapper):
    assert call({"payload": {wrapper: definition}})["code"] == "invalid_input"


@pytest.mark.parametrize("wrapper", ["definition", "arguments"])
def test_envelope_preserves_authentication_requirement(definition, wrapper):
    assert call({wrapper: definition}, session_id=None)["code"] == "session_required"
