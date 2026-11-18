"""Chat adapter for the structured arithmetic engine; with session-scoped snapshots."""

from __future__ import annotations

import json

from pydantic import ValidationError

from app.simulation.conversation import ConversationError, owned_session, save_result
from app.simulation.engine import SimulationError, run_simulation
from app.simulation.schemas import SimulationDefinition
from app.tools.context import ToolContext

MAX_INPUT_BYTES = 32 * 1024
MAX_RESULT_BYTES = 128 * 1024
MAX_PARAMETERS = 32
MAX_OUTPUTS = 8
MAX_SCENARIOS = 8

_WRAPPERS = {"definition", "arguments"}
_INPUT_HINT = (
    "Send title and objective as strings; parameters, outputs and scenarios as arrays "
    "of objects at the top level. Parameter baselineValue and override value must be "
    "finite numbers, not strings. Do not invent missing values. Follow the tool schema. "
    "A single definition or arguments object wrapper is accepted, without siblings or nesting."
)


def _error(code: str, message: str, **details) -> str:
    return json.dumps(
        {"status": "error", "code": code, "message": message, **details},
        ensure_ascii=False,
    )


def _input_error(message: str, **details) -> str:
    return _error("invalid_input", message, hint=_INPUT_HINT, **details)


def _definition_args(args: dict) -> dict:
    """Unwrap only a single known envelope; never coerce or merge field values."""
    wrappers = _WRAPPERS.intersection(args)
    if not wrappers:
        return args
    if len(args) != 1:
        raise ValueError("A definition/arguments wrapper must be the only top-level field")
    definition = args[next(iter(wrappers))]
    if not isinstance(definition, dict):
        raise TypeError("The definition/arguments wrapper must contain an object")
    if _WRAPPERS.intersection(definition):
        raise ValueError("Nested definition/arguments wrappers are not supported")
    return definition


class RunSimulation:
    name = "run_simulation"

    def schema(self) -> dict:
        parameters = SimulationDefinition.model_json_schema(by_alias=True)
        parameters["properties"]["parameters"]["maxItems"] = MAX_PARAMETERS
        parameters["properties"]["outputs"]["maxItems"] = MAX_OUTPUTS
        parameters["properties"]["scenarios"]["maxItems"] = MAX_SCENARIOS
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Run a structured what-if simulation and compare alternatives against one "
                    "baseline. Use this for scenario arithmetic instead of execute_code. "
                    "Supply the complete definition directly at the top level using camelCase fields: parameters with "
                    "baselineValue, unit, origin; outputs with expression; and scenarios with "
                    "overrides mapping parameter IDs to {value, origin}. Origin kind is "
                    "user_input, referenced_data (requires reference), assumption, or synthetic. "
                    "Use known user-provided or retrieved values; never invent factual data or "
                    "source references. Ask for missing inputs or relationships; explicitly label "
                    "user-authorized assumptions and synthetic examples. Expressions support only "
                    "numbers, parameter IDs, + - * /, unary signs and parentheses, not functions "
                    "or references to other outputs. For 'increase by 20%', convert to an absolute "
                    "override value using the stated baseline. Explain results using returned "
                    "numbers, units, assumptions and limitations; do not treat arithmetic as "
                    "validated policy impact. If status is error, clarify or correct the input "
                    "and do not claim success. Successful definitions are saved per session. "
                    "Only a sole scenario becomes active automatically. Multiple alternatives are saved "
                    "without pending selection; a comparison can finish without choosing a continuation. "
                    "Ask for a reference only on an ambiguous follow-up, using clarify_simulation. "
                    "Do not append old comparisons as if they were the active requested scenario. "
                    "Use clarify_simulation for ambiguous additive follow-ups; do not guess their reference. "
                    "Limits: 32 KiB input, 32 parameters, 8 outputs, 8 scenarios."
                ),
                "parameters": parameters,
            },
        }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        if not ctx.session_id:
            return _error("session_required", "Simulation requires an authenticated chat session")
        try:
            session = owned_session(ctx)
            if (session.simulation_state or {}).get("pending"):
                return _error(
                    "clarification_pending", "Resolve or cancel the active clarification first"
                )
        except ConversationError as exc:
            return _error("session_required", str(exc))
        if not isinstance(args, dict):
            return _input_error("Simulation definition must be an object")
        try:
            encoded = json.dumps(args, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return _input_error("Simulation definition must contain finite JSON values")
        if len(encoded) > MAX_INPUT_BYTES:
            return _error("input_limit", "Definition exceeds 32 KiB; reduce its size")
        # Limit the original envelope before opening it, then apply all existing
        # collection/schema/engine checks to its contents. Never discard siblings.
        try:
            args = _definition_args(args)
        except (TypeError, ValueError) as exc:
            return _input_error(str(exc))
        for field, limit in (
            ("parameters", MAX_PARAMETERS),
            ("outputs", MAX_OUTPUTS),
            ("scenarios", MAX_SCENARIOS),
        ):
            collection = args.get(field)
            if isinstance(collection, list) and len(collection) > limit:
                return _error("input_limit", f"At most {limit} {field} are supported")
        try:
            definition = SimulationDefinition.model_validate(args)
        except ValidationError as exc:
            issues = [
                {
                    "field": ".".join(str(part) for part in issue["loc"]),
                    "type": issue["type"],
                    "message": issue["msg"][:300],
                }
                for issue in exc.errors()[:5]
            ]
            return _input_error("Correct the simulation definition", issues=issues)
        try:
            result = run_simulation(definition)
        except SimulationError as exc:
            return _error(exc.code, str(exc), outputId=exc.output_id, scenarioId=exc.scenario_id)
        response = json.dumps(
            {
                "status": "success",
                "result": result.model_dump(mode="json", by_alias=True),
                "activeScenarioId": result.scenarios[0].id if len(result.scenarios) == 1 else None,
                "requiresScenarioSelection": False,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(response.encode("utf-8")) > MAX_RESULT_BYTES:
            return _error("result_limit", "Result exceeds 128 KiB; reduce parameters or scenarios")
        save_result(ctx, result)
        return response
