"""Arithmetic, isolation, resource bounds, and manual runner behavior."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.simulation.engine import SimulationError, run_simulation
from app.simulation.schemas import SimulationDefinition, SimulationResult

BACKEND = Path(__file__).resolve().parents[1]
EXAMPLE = BACKEND / "examples" / "simulation" / "fleet.json"


@pytest.fixture
def payload():
    return json.loads(EXAMPLE.read_text("utf-8"))


def run(payload):
    return run_simulation(SimulationDefinition.model_validate(payload))


def test_reference_calculations_and_independent_alternatives(payload):
    result = run(payload)
    assert result.baseline.outputs == {"total": 50}
    first, second = result.scenarios
    assert first.outputs["total"].scenario_value == 60
    assert first.outputs["total"].delta == 10
    assert first.outputs["total"].percentage_change == 20
    assert second.outputs["total"].scenario_value == 75
    assert second.outputs["total"].delta == 25
    assert second.outputs["total"].percentage_change == 50


def test_effective_inputs_keep_origins_and_do_not_leak_between_scenarios(payload):
    payload["scenarios"][0]["overrides"]["capacity"] = {
        "value": 2,
        "origin": {"kind": "assumption", "description": "Reduced capacity"},
    }
    result = run(payload)
    assert result.scenarios[0].outputs["total"].scenario_value == 24
    assert result.scenarios[1].outputs["total"].scenario_value == 75
    assert result.scenarios[0].parameters["capacity"].origin.kind == "assumption"
    assert result.scenarios[1].parameters["capacity"].origin.kind == "synthetic"


def test_deterministic_detached_result_and_wire_round_trip(payload):
    original = SimulationDefinition.model_validate(payload)
    before = original.model_dump()
    result = run_simulation(original)
    assert result == run_simulation(original)
    assert SimulationResult.model_validate_json(result.model_dump_json(by_alias=True)) == result
    result.definition.parameters[0].baseline_value = 99
    result.scenarios[0].parameters["capacity"].value = 99
    assert original.model_dump() == before
    assert result.baseline.parameters["capacity"].value == 5
    assert result.scenarios[1].parameters["capacity"].value == 5


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("fleet + capacity * 2", 20),
        ("(fleet + capacity) * 2", 30),
        ("-fleet + +capacity", -5),
        ("fleet / capacity - 0.5", 1.5),
        ("1e2 + 2.5", 102.5),
        ("fleet - fleet", 0),
        ("capacity / 3", 5 / 3),
    ],
)
def test_supported_arithmetic_without_display_rounding(payload, expression, expected):
    payload["outputs"][0]["expression"] = expression
    assert run(payload).baseline.outputs["total"] == expected


def test_multiple_outputs(payload):
    output = deepcopy(payload["outputs"][0])
    output.update(id="double", expression="fleet * capacity * 2")
    payload["outputs"].append(output)
    result = run(payload)
    assert result.baseline.outputs == {"total": 50, "double": 100}
    assert result.scenarios[0].outputs["double"].delta == 20


def test_zero_baseline_has_null_percentage_and_numeric_delta(payload):
    payload["parameters"][0]["baselineValue"] = 0
    result = run(payload)
    comparison = result.scenarios[0].outputs["total"]
    assert comparison.delta == 60
    assert comparison.percentage_change is None
    assert '"percentageChange":null' in result.model_dump_json(by_alias=True)


def test_negative_baseline_uses_signed_denominator(payload):
    payload["parameters"][0].update(baselineValue=-10, minimum=-100)
    payload["outputs"][0]["expression"] = "fleet"
    comparison = run(payload).scenarios[0].outputs["total"]
    assert comparison.delta == 22
    assert comparison.percentage_change == pytest.approx(-220)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').getcwd()",
        "open('/tmp/never-opened')",
        "fleet.real",
        "[fleet][0]",
        "[x for x in [fleet]]",
        "lambda: fleet",
        "fleet if True else 0",
        "fleet ** 2",
        "fleet // 2",
        "fleet % 2",
        "fleet > 2",
        "fleet and capacity",
        "True",
        "None",
        "'text'",
        "1j",
        "[1, 2]",
        "{'x': 1}",
    ],
)
def test_rejects_non_arithmetic_constructs(payload, expression):
    payload["outputs"][0]["expression"] = expression
    with pytest.raises(SimulationError) as exc:
        run(payload)
    assert exc.value.code == "unsupported_expression"
    assert exc.value.output_id == "total"


@pytest.mark.parametrize(
    "expression,code",
    [
        ("fleet +", "invalid_expression"),
        ("fleet\x00", "invalid_expression"),
        ("missing * capacity", "unknown_parameter"),
        ("total + 1", "unknown_parameter"),
        ("1e309", "non_finite_result"),
        ("9" * 400, "non_finite_result"),
    ],
)
def test_rejects_invalid_symbols_syntax_and_constants(payload, expression, code):
    payload["outputs"][0]["expression"] = expression
    with pytest.raises(SimulationError) as exc:
        run(payload)
    assert exc.value.code == code


@pytest.mark.parametrize("kind", ["length", "depth", "nodes"])
def test_expression_resource_limits(payload, kind):
    if kind == "length":
        expression = "1" * 1025
    elif kind == "depth":
        expression = "-" * 30 + "1"
    else:
        expression = "1"
        for _ in range(6):
            expression = f"({expression}+{expression})"
    payload["outputs"][0]["expression"] = expression
    with pytest.raises(SimulationError) as exc:
        run(payload)
    assert exc.value.code == "expression_limit"


@pytest.mark.parametrize("scenario_zero", [False, True])
def test_division_error_includes_output_and_scope(payload, scenario_zero):
    payload["outputs"][0]["expression"] = "capacity / fleet"
    if scenario_zero:
        payload["scenarios"][0]["overrides"]["fleet"]["value"] = 0
    else:
        payload["parameters"][0]["baselineValue"] = 0
    with pytest.raises(SimulationError) as exc:
        run(payload)
    assert exc.value.code == "division_by_zero"
    assert exc.value.output_id == "total"
    assert exc.value.scenario_id == ("add_two" if scenario_zero else None)


@pytest.mark.parametrize("kind", ["intermediate", "delta", "percentage"])
def test_non_finite_calculations_and_comparisons_are_rejected(payload, kind):
    parameter = payload["parameters"][0]
    parameter.pop("maximum")
    if kind == "intermediate":
        parameter["baselineValue"] = 1e308
    elif kind == "delta":
        parameter.update(baselineValue=-1e308, minimum=-1e308)
        payload["scenarios"][0]["overrides"]["fleet"]["value"] = 1e308
        payload["outputs"][0]["expression"] = "fleet"
    else:
        parameter["baselineValue"] = 1e-308
        payload["outputs"][0]["expression"] = "fleet"
    with pytest.raises(SimulationError) as exc:
        run(payload)
    assert exc.value.code == "non_finite_result"


def test_failure_in_later_scenario_does_not_return_partial_success(payload):
    payload["outputs"][0]["expression"] = "capacity / fleet"
    payload["scenarios"][1]["overrides"]["fleet"]["value"] = 0
    with pytest.raises(SimulationError) as exc:
        run(payload)
    assert exc.value.scenario_id == "add_five"


def test_mutated_model_is_revalidated_at_engine_boundary(payload):
    model = SimulationDefinition.model_validate(payload)
    model.scenarios[0].overrides["fleet"].value = -1
    with pytest.raises(ValidationError, match="minimum"):
        run_simulation(model)


def test_manual_runner_success():
    process = subprocess.run(
        [sys.executable, "-m", "app.simulation", str(EXAMPLE)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert process.stderr == ""
    result = json.loads(process.stdout)
    assert result["baseline"]["outputs"]["total"] == 50
    assert result["scenarios"][0]["outputs"]["total"]["percentageChange"] == 20


@pytest.mark.parametrize("kind", ["invalid_json", "missing_file", "calculation"])
def test_manual_runner_errors_are_nonzero_without_success_output(payload, tmp_path, kind):
    source = tmp_path / "scenario.json"
    if kind == "invalid_json":
        source.write_text("{", encoding="utf-8")
    elif kind == "calculation":
        payload["outputs"][0]["expression"] = "fleet / 0"
        source.write_text(json.dumps(payload), encoding="utf-8")
    process = subprocess.run(
        [sys.executable, "-m", "app.simulation", str(source)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 2
    assert process.stdout == ""
    error = json.loads(process.stderr)
    assert error["code"] == ("division_by_zero" if kind == "calculation" else "invalid_input")
