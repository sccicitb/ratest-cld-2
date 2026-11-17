"""Markdown presentation of calculated results. Never calculates new comparisons."""

from __future__ import annotations

import html
import re

from app.simulation.schemas import SimulationResult, ValueOrigin

_ORIGINS = {
    "user_input": "Input pengguna",
    "synthetic": "Data sintetis",
    "assumption": "Asumsi",
    "referenced_data": "Data bersumber",
}


def _text(value: str) -> str:
    # Keep model-authored metadata inside its cell/heading instead of interpreting
    # it as Markdown structure or HTML. Newlines must not create table rows.
    value = html.escape(" ".join(value.split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]|#+])", r"\\\1", value)


def _number(value: float) -> str:
    # Use Python's round-trip float representation: no hidden six-digit rounding,
    # no new delta/percentage arithmetic, and normalize signed zero for display.
    if value == 0:
        return "0"
    text = str(value)
    return text.removesuffix(".0")


def _origin(origin: ValueOrigin) -> str:
    text = f"{_ORIGINS[origin.kind]}: {_text(origin.description)}"
    if origin.reference:
        text += f"; referensi: {_text(origin.reference)}"
    return text


def render_simulation(result: SimulationResult) -> str:
    """Use the exact engine fields for baseline, alternatives, delta and percent."""
    definition = result.definition
    lines = [
        f"**{_text(definition.title)}**",
        "",
        _text(definition.objective),
        "",
        "Hasil dihitung oleh engine simulasi.",
        "",
        "**Rumus**",
        "",
        "| Hasil | Satuan | Rumus |",
        "|---|---|---|",
    ]
    for output in definition.outputs:
        lines.append(
            f"| {_text(output.label)} | {_text(output.unit)} | {_text(output.expression)} |"
        )
    lines += [
        "",
        "**Parameter baseline**",
        "",
        "| Parameter | Nilai | Satuan | Asal nilai |",
        "|---|---:|---|---|",
    ]
    for parameter in definition.parameters:
        value = result.baseline.parameters[parameter.id]
        lines.append(
            f"| {_text(parameter.label)} | {_number(value.value)} | {_text(parameter.unit)} | {_origin(value.origin)} |"
        )
    for scenario in result.scenarios:
        lines += [
            "",
            f"**Skenario: {_text(scenario.label)}**",
            "",
            "| Parameter efektif | Nilai | Satuan | Asal nilai |",
            "|---|---:|---|---|",
        ]
        for parameter in definition.parameters:
            value = scenario.parameters[parameter.id]
            lines.append(
                f"| {_text(parameter.label)} | {_number(value.value)} | {_text(parameter.unit)} | {_origin(value.origin)} |"
            )
        lines += [
            "",
            "| Hasil | Satuan | Baseline | Skenario | Selisih | Perubahan terhadap baseline |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for output in definition.outputs:
            comparison = scenario.outputs[output.id]
            percent = (
                "Tidak terdefinisi (baseline nol)"
                if comparison.percentage_change is None
                else _number(comparison.percentage_change) + "%"
            )
            lines.append(
                f"| {_text(output.label)} | {_text(output.unit)} | {_number(comparison.baseline_value)} | {_number(comparison.scenario_value)} | {_number(comparison.delta)} | {percent} |"
            )
    lines += ["", "**Asumsi**", ""]
    lines.extend(f"- {_text(item)}" for item in definition.assumptions)
    if not definition.assumptions:
        lines.append("Tidak dicantumkan dalam definisi simulasi.")
    lines += ["", "**Keterbatasan**", ""]
    lines.extend(f"- {_text(item)}" for item in definition.limitations)
    lines.append(
        "- Hasil mengikuti data dan rumus yang diberikan; bukan bukti dampak kausal kebijakan."
    )
    return "\n".join(lines)


def render_reference_change(
    *, reference: str, parameter: str, unit: str, before: float, amount: float, after: float
) -> str:
    """Describe already-computed inputs without rounding or interpreting labels."""
    return (
        f"Acuan: {_text(reference)}. {_text(parameter)}: "
        f"{_number(before)} + {_number(amount)} = **{_number(after)} {_text(unit)}**."
    )
