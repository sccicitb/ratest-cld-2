"""Model adapters: interpretation belongs to the model; arithmetic to the server."""

import json

from pydantic import Field, StrictFloat, ValidationError

from app.simulation.conversation import (
    request_clarification,
    resolve_clarification,
)
from app.simulation.schemas import Identifier, SimulationModel


class RequestClarification(SimulationModel):
    parameter_id: Identifier | None = None
    amount: StrictFloat | None = None


class ResolveClarification(SimulationModel):
    question_id: str = Field(min_length=1, max_length=64)
    reference: str | None = Field(default=None, min_length=1, max_length=80)
    amount: StrictFloat | None = None
    cancel: bool = False


class ClarifySimulation:
    name = "clarify_simulation"

    def schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Ask clickable clarification when an additive follow-up (e.g. tambah lima) has ambiguous baseline vs last-scenario reference. Requires a previously saved simulation. To ask which existing scenario should become active without calculating, omit parameterId and amount. Supply the parameter ID and additive amount ONLY; the server builds and anchors options. Do not choose a reference yourself. One active question per session.",
                "parameters": RequestClarification.model_json_schema(by_alias=True),
            },
        }

    async def execute(self, args, ctx):
        try:
            parsed = RequestClarification.model_validate(args)
            pending = request_clarification(ctx, parsed.parameter_id, parsed.amount)
            return json.dumps(
                {"status": "needs_clarification", "pending": pending}, ensure_ascii=False
            )
        except ValidationError:
            return json.dumps(
                {
                    "status": "error",
                    "message": "Invalid clarification fields; check parameterId and finite amount.",
                }
            )
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)[:500]}, ensure_ascii=False)


class ResolveSimulationClarification:
    name = "resolve_simulation_clarification"

    def schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Resolve the ACTIVE stored clarification from the user's natural answer. Map ordinal answers to the EXACT option id in the ACTIVE pending options, never hardcode an ordinal as last_scenario. reference is baseline, last_scenario (only when offered for an explicitly identified active scenario), or scenario:<id>. A pending select_scenario only selects an identity; it does not calculate. If the user changes the increment, supply amount (e.g. dari terakhir tapi tambah tiga => last_scenario, amount=3). Never infer a reference when still ambiguous: ask again instead. For cancellation set cancel=true. questionId MUST match active context. Backend computes results; do not run_simulation to bypass this.",
                "parameters": ResolveClarification.model_json_schema(by_alias=True),
            },
        }

    async def execute(self, args, ctx):
        try:
            parsed = ResolveClarification.model_validate(args)
            result = resolve_clarification(
                ctx, parsed.question_id, parsed.reference, parsed.amount, parsed.cancel
            )
            return json.dumps(result, ensure_ascii=False, allow_nan=False)
        except ValidationError:
            return json.dumps(
                {
                    "status": "error",
                    "message": "Invalid resolution fields; check questionId, reference and finite amount.",
                }
            )
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)[:500]}, ensure_ascii=False)
