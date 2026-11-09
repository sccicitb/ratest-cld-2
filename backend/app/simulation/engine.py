"""Bounded arithmetic evaluation for structured what-if simulations.

AST nodes are interpreted directly. No eval, exec, compiled Python, tool calls,
database access, or sandbox service is involved. Units and causal assumptions
remain declarations; successful arithmetic does not validate a policy model.
"""

from __future__ import annotations

import ast
import math
import operator

from app.simulation.schemas import (
    BaselineResult,
    EffectiveParameter,
    OutputComparison,
    ScenarioResult,
    SimulationDefinition,
    SimulationResult,
)

MAX_EXPRESSION_LENGTH = 1024
MAX_EXPRESSION_NODES = 128
MAX_EXPRESSION_DEPTH = 24

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ALLOWED = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
    *_BINARY,
    *_UNARY,
)


class SimulationError(ValueError):
    """Domain error; a future tool/API adapter can translate it for chat."""

    def __init__(
        self, code: str, message: str, *, output_id: str, scenario_id: str | None = None
    ) -> None:
        self.code = code
        self.output_id = output_id
        self.scenario_id = scenario_id
        scope = "baseline" if scenario_id is None else f"scenario {scenario_id}"
        super().__init__(f"output {output_id}, {scope}: {message}")


def _parse(expression: str, parameter_ids: set[str], output_id: str) -> ast.Expression:
    def fail(code: str, message: str) -> SimulationError:
        return SimulationError(code, message, output_id=output_id)

    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise fail("expression_limit", "expression exceeds length limit")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise fail("invalid_expression", "invalid arithmetic expression") from exc

    stack = [(tree, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_EXPRESSION_NODES or depth > MAX_EXPRESSION_DEPTH:
            raise fail("expression_limit", "expression exceeds complexity limit")
        if type(node) not in _ALLOWED:
            raise fail(
                "unsupported_expression", f"unsupported expression node: {type(node).__name__}"
            )
        if isinstance(node, ast.Name) and node.id not in parameter_ids:
            raise fail("unknown_parameter", f"unknown parameter: {node.id}")
        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                raise fail("unsupported_expression", "only numeric constants are allowed")
            try:
                finite = math.isfinite(float(node.value))
            except OverflowError:
                finite = False
            if not finite:
                raise fail("non_finite_result", "numeric constant is not finite")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree


def _finite(value: float, output_id: str, scenario_id: str | None) -> float:
    if not math.isfinite(value):
        raise SimulationError(
            "non_finite_result",
            "calculation produced a non-finite number",
            output_id=output_id,
            scenario_id=scenario_id,
        )
    return value


def _evaluate(
    node: ast.AST, values: dict[str, float], output_id: str, scenario_id: str | None
) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, values, output_id, scenario_id)
    if isinstance(node, ast.Constant):
        result = float(node.value)
    elif isinstance(node, ast.Name):
        result = values[node.id]
    elif isinstance(node, ast.UnaryOp):
        result = _UNARY[type(node.op)](_evaluate(node.operand, values, output_id, scenario_id))
    else:  # Only validated BinOp nodes can reach this branch.
        left = _evaluate(node.left, values, output_id, scenario_id)
        right = _evaluate(node.right, values, output_id, scenario_id)
        try:
            result = _BINARY[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise SimulationError(
                "division_by_zero",
                "division by zero",
                output_id=output_id,
                scenario_id=scenario_id,
            ) from exc
    return _finite(result, output_id, scenario_id)


def run_simulation(definition: SimulationDefinition) -> SimulationResult:
    """Calculate all outputs or raise; never return partial results.

    Revalidation and a detached snapshot protect against mutated Pydantic inputs.
    Each alternative starts from baseline, never from the preceding alternative.
    Percent changes use the signed baseline denominator; zero gives None.
    """
    definition = SimulationDefinition.model_validate(definition.model_dump())
    baseline_parameters = {
        p.id: EffectiveParameter(value=p.baseline_value, origin=p.origin)
        for p in definition.parameters
    }
    trees = {
        output.id: _parse(output.expression, set(baseline_parameters), output.id)
        for output in definition.outputs
    }
    baseline_values = {key: p.value for key, p in baseline_parameters.items()}
    baseline_outputs = {
        key: _evaluate(tree, baseline_values, key, None) for key, tree in trees.items()
    }

    scenarios = []
    for scenario in definition.scenarios:
        parameters = {key: p.model_copy(deep=True) for key, p in baseline_parameters.items()}
        for key, override in scenario.overrides.items():
            parameters[key] = EffectiveParameter(value=override.value, origin=override.origin)
        values = {key: p.value for key, p in parameters.items()}
        outputs = {}
        for key, tree in trees.items():
            baseline = baseline_outputs[key]
            value = _evaluate(tree, values, key, scenario.id)
            delta = _finite(value - baseline, key, scenario.id)
            percentage = (
                _finite((delta / baseline) * 100, key, scenario.id) if baseline != 0 else None
            )
            outputs[key] = OutputComparison(
                baseline_value=baseline,
                scenario_value=value,
                delta=delta,
                percentage_change=percentage,
            )
        scenarios.append(
            ScenarioResult(
                id=scenario.id,
                label=scenario.label,
                parameters=parameters,
                outputs=outputs,
            )
        )

    return SimulationResult(
        definition=definition,
        baseline=BaselineResult(parameters=baseline_parameters, outputs=baseline_outputs),
        scenarios=scenarios,
    )
