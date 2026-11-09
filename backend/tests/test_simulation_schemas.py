"""Structural simulation validation; no app startup or model services needed."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.simulation.schemas import SimulationDefinition


@pytest.fixture
def definition():
    origin = {"kind": "synthetic", "description": "Example values, not OPD data"}
    return {
        "title": "Fleet capacity",
        "objective": "Compare daily capacity after adding vehicles",
        "parameters": [
            {
                "id": "fleet",
                "label": "Vehicles",
                "unit": "vehicle",
                "baselineValue": 10,
                "minimum": 0,
                "maximum": 100,
                "origin": origin,
            },
            {
                "id": "capacity",
                "label": "Capacity per vehicle",
                "unit": "ton/day/vehicle",
                "baselineValue": 5,
                "minimum": 0,
                "origin": origin,
            },
        ],
        "outputs": [
            {
                "id": "total",
                "label": "Total capacity",
                "unit": "ton/day",
                "expression": "fleet * capacity",
            },
        ],
        "scenarios": [
            {
                "id": "add_two",
                "label": "Add two vehicles",
                "overrides": {"fleet": {"value": 12, "origin": origin}},
            },
        ],
        "assumptions": ["All vehicles have equal capacity"],
        "limitations": ["Routes and downtime are excluded"],
    }


def test_round_trip_preserves_baseline_overrides_and_provenance(definition):
    model = SimulationDefinition.model_validate(definition)
    wire = model.model_dump(mode="json", by_alias=True)
    assert wire["schemaVersion"] == "1"
    assert wire["parameters"][0]["baselineValue"] == 10
    assert wire["scenarios"][0]["overrides"]["fleet"]["value"] == 12
    assert wire["scenarios"][0]["overrides"]["fleet"]["origin"]["kind"] == "synthetic"
    assert SimulationDefinition.model_validate(wire) == model


def test_accepts_snake_case_and_inclusive_bounds(definition):
    parameter = definition["parameters"][0]
    parameter["baseline_value"] = parameter.pop("baselineValue")
    parameter["baseline_value"] = 0
    definition["scenarios"][0]["overrides"]["fleet"]["value"] = 100
    assert SimulationDefinition.model_validate(definition).parameters[0].baseline_value == 0


@pytest.mark.parametrize("collection", ["parameters", "outputs", "scenarios"])
def test_rejects_empty_or_duplicate_collections(definition, collection):
    duplicate = deepcopy(definition[collection][0])
    definition[collection].append(duplicate)
    with pytest.raises(ValidationError, match="duplicate"):
        SimulationDefinition.model_validate(definition)
    definition[collection] = []
    with pytest.raises(ValidationError):
        SimulationDefinition.model_validate(definition)


@pytest.mark.parametrize("value", [True, "10", float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("location", ["baseline", "override", "minimum", "maximum"])
def test_rejects_non_numeric_or_non_finite_values(definition, value, location):
    if location == "override":
        definition["scenarios"][0]["overrides"]["fleet"]["value"] = value
    else:
        key = "baselineValue" if location == "baseline" else location
        definition["parameters"][0][key] = value
    with pytest.raises(ValidationError):
        SimulationDefinition.model_validate(definition)


@pytest.mark.parametrize(
    "location,value", [("baseline", -1), ("baseline", 101), ("override", -1), ("override", 101)]
)
def test_rejects_out_of_bounds_values(definition, location, value):
    if location == "baseline":
        definition["parameters"][0]["baselineValue"] = value
    else:
        definition["scenarios"][0]["overrides"]["fleet"]["value"] = value
    with pytest.raises(ValidationError, match="minimum|maximum"):
        SimulationDefinition.model_validate(definition)


def test_rejects_inverted_bounds(definition):
    definition["parameters"][0].update(minimum=20, maximum=10)
    with pytest.raises(ValidationError, match="minimum exceeds maximum"):
        SimulationDefinition.model_validate(definition)


def test_rejects_unknown_override_parameter(definition):
    overrides = definition["scenarios"][0]["overrides"]
    overrides["unknown"] = overrides.pop("fleet")
    with pytest.raises(ValidationError, match="unknown parameter"):
        SimulationDefinition.model_validate(definition)


def test_rejects_empty_overrides(definition):
    definition["scenarios"][0]["overrides"] = {}
    with pytest.raises(ValidationError):
        SimulationDefinition.model_validate(definition)


def test_rejects_parameter_output_name_collision(definition):
    definition["outputs"][0]["id"] = "fleet"
    with pytest.raises(ValidationError, match="must not overlap"):
        SimulationDefinition.model_validate(definition)


@pytest.mark.parametrize(
    "field,value", [("title", "  "), ("schemaVersion", "2"), ("userId", "untrusted-user")]
)
def test_rejects_blank_title_wrong_version_and_extra_fields(definition, field, value):
    definition[field] = value
    with pytest.raises(ValidationError):
        SimulationDefinition.model_validate(definition)


def test_referenced_data_requires_reference(definition):
    origin = {"kind": "referenced_data", "description": "Annual report"}
    definition["parameters"][0]["origin"] = origin
    with pytest.raises(ValidationError, match="source reference"):
        SimulationDefinition.model_validate(definition)
    origin["reference"] = "report-2026#table-1"
    assert SimulationDefinition.model_validate(definition).parameters[0].origin.reference


def test_expressions_are_only_declarations_at_this_stage(definition):
    definition["outputs"][0]["expression"] = "missing_parameter / 0"
    model = SimulationDefinition.model_validate(definition)
    assert model.outputs[0].expression == "missing_parameter / 0"


def test_nested_unknown_fields_and_invalid_identifiers_rejected(definition):
    definition["parameters"][0]["id"] = "not an identifier"
    definition["outputs"][0]["unexpected"] = True
    with pytest.raises(ValidationError) as exc:
        SimulationDefinition.model_validate(definition)
    assert len(exc.value.errors()) == 2
