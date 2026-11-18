"""Scenario contract, independent of the LLM, database, and calculation engine.

Parameter baseline values define the initial state. Each alternative overrides
only named parameters; all other values remain at baseline. Expressions are
declarations at this stage: they are never parsed or executed by these schemas.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictFloat, model_validator

from app.schema_base import CamelModel

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
NonEmptyText = Annotated[str, Field(min_length=1)]


class SimulationModel(CamelModel):
    """Keep the application's camelCase wire format and reject unknown fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class ValueOrigin(SimulationModel):
    """Declared provenance, not proof that an external source was verified."""

    kind: Literal["user_input", "referenced_data", "assumption", "synthetic"]
    description: NonEmptyText
    reference: NonEmptyText | None = None

    @model_validator(mode="after")
    def require_data_reference(self) -> ValueOrigin:
        if self.kind == "referenced_data" and self.reference is None:
            raise ValueError("referenced_data requires a source reference")
        return self


class SimulationParameter(SimulationModel):
    id: Identifier
    label: NonEmptyText
    unit: NonEmptyText
    baseline_value: StrictFloat
    origin: ValueOrigin
    minimum: StrictFloat | None = None
    maximum: StrictFloat | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> SimulationParameter:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError(f"parameter {self.id}: minimum exceeds maximum")
        self.check_value(self.baseline_value)
        return self

    def check_value(self, value: float) -> None:
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"parameter {self.id}: value is below minimum")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"parameter {self.id}: value exceeds maximum")


class SimulationOutput(SimulationModel):
    id: Identifier
    label: NonEmptyText
    unit: NonEmptyText
    expression: NonEmptyText


class ParameterOverride(SimulationModel):
    value: StrictFloat
    origin: ValueOrigin


class SimulationScenario(SimulationModel):
    id: Identifier
    label: NonEmptyText
    overrides: dict[Identifier, ParameterOverride] = Field(min_length=1)


class SimulationDefinition(SimulationModel):
    """A complete structural definition; engine validation is still required.

    This model checks scenario-to-parameter references, but does not check symbols
    inside expressions, dimensional consistency, or causal validity of a formula.
    User/session ownership belongs to server context, never model-generated input.
    """

    schema_version: Literal["1"] = "1"
    title: NonEmptyText
    objective: NonEmptyText
    parameters: list[SimulationParameter] = Field(min_length=1)
    outputs: list[SimulationOutput] = Field(min_length=1)
    scenarios: list[SimulationScenario] = Field(min_length=1)
    assumptions: list[NonEmptyText] = Field(default_factory=list)
    limitations: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> SimulationDefinition:
        parameter_ids = [p.id for p in self.parameters]
        output_ids = [o.id for o in self.outputs]
        scenario_ids = [s.id for s in self.scenarios]
        for label, ids in (
            ("parameter", parameter_ids),
            ("output", output_ids),
            ("scenario", scenario_ids),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} ids")
        if set(parameter_ids) & set(output_ids):
            raise ValueError("parameter and output ids must not overlap")

        parameters = {p.id: p for p in self.parameters}
        for scenario in self.scenarios:
            for parameter_id, override in scenario.overrides.items():
                if parameter_id not in parameters:
                    raise ValueError(f"scenario {scenario.id}: unknown parameter {parameter_id}")
                parameters[parameter_id].check_value(override.value)
        return self


class EffectiveParameter(SimulationModel):
    """A concrete input value and its declared origin for one calculation."""

    value: StrictFloat
    origin: ValueOrigin


class BaselineResult(SimulationModel):
    parameters: dict[Identifier, EffectiveParameter]
    outputs: dict[Identifier, StrictFloat]


class OutputComparison(SimulationModel):
    baseline_value: StrictFloat
    scenario_value: StrictFloat
    delta: StrictFloat
    percentage_change: StrictFloat | None


class ScenarioResult(SimulationModel):
    id: Identifier
    label: NonEmptyText
    parameters: dict[Identifier, EffectiveParameter]
    outputs: dict[Identifier, OutputComparison]


class SimulationResult(SimulationModel):
    definition: SimulationDefinition
    baseline: BaselineResult
    scenarios: list[ScenarioResult]


class SimulationReport(SimulationModel):
    """Message-owned display payload; references one completed engine calculation."""

    result: SimulationResult
    reference_change: str | None = None
