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


def _signed(value: float) -> str:
    return ("+" if value > 0 else "") + _number(value)


def render_simulation(result: SimulationResult) -> str:
    """Lead with comparisons; retain formulas and provenance without repeating inputs."""
    definition = result.definition
    origin_kinds = {value.origin.kind for value in result.baseline.parameters.values()}
    origin_kinds.update(
        value.origin.kind for scenario in result.scenarios for value in scenario.parameters.values()
    )
    origin_summary = " · ".join(label for kind, label in _ORIGINS.items() if kind in origin_kinds)
    lines = [
        f"### {_text(definition.title)}",
        "",
        _text(definition.objective),
        "",
        f"Asal input: {origin_summary}.",
        "",
        "Perbandingan terhadap **kondisi awal (baseline)**. Hasil dihitung oleh engine simulasi.",
        "",
        "**Kondisi awal:** "
        + "; ".join(
            f"{_text(p.label)}: {_number(result.baseline.parameters[p.id].value)} {_text(p.unit)}"
            for p in definition.parameters
        )
        + ".",
    ]
    for output in definition.outputs:
        lines += [
            "",
            f"#### {_text(output.label)} ({_text(output.unit)})",
            "",
            "| Skenario | Hasil | Selisih | Perubahan |",
            "|---|---:|---:|---:|",
            f"| Kondisi awal | **{_number(result.baseline.outputs[output.id])}** | — | — |",
        ]
        for scenario in result.scenarios:
            comparison = scenario.outputs[output.id]
            percent = (
                "Tidak terdefinisi (baseline nol)"
                if comparison.percentage_change is None
                else _signed(comparison.percentage_change) + "%"
            )
            lines.append(
                f"| {_text(scenario.label)} | **{_number(comparison.scenario_value)}** | {_signed(comparison.delta)} | {percent} |"
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
    lines += [
        "",
        "#### Detail perhitungan dan asal nilai",
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
    for scenario in definition.scenarios:
        lines += [
            "",
            f"**Perubahan input: {_text(scenario.label)}**",
            "",
            "| Parameter | Nilai | Satuan | Asal nilai |",
            "|---|---:|---|---|",
        ]
        changed = False
        for parameter in definition.parameters:
            if parameter.id in scenario.overrides:
                value = scenario.overrides[parameter.id]
                baseline = result.baseline.parameters[parameter.id]
                # Follow-ups carry all reference inputs. Hide identical carried
                # values, but retain an explicitly changed origin for traceability.
                if value.value == baseline.value and value.origin == baseline.origin:
                    continue
                changed = True
                lines.append(
                    f"| {_text(parameter.label)} | {_number(value.value)} | {_text(parameter.unit)} | {_origin(value.origin)} |"
                )
        if not changed:
            lines.append("| Sama dengan kondisi awal | — | — | Nilai dan asal input tetap |")
    lines += [
        "",
        "Input yang tidak ditampilkan ulang memiliki nilai dan asal yang sama dengan kondisi awal. Asal nilai merupakan deklarasi input, bukan verifikasi sumber otomatis.",
    ]
    return "\n".join(lines)


def render_reference_change(
    *, reference: str, parameter: str, unit: str, before: float, amount: float, after: float
) -> str:
    """Describe already-computed inputs without rounding or interpreting labels."""
    return (
        f"Acuan: {_text(reference)}. {_text(parameter)}: "
        f"{_number(before)} + {_number(amount)} = **{_number(after)} {_text(unit)}**."
    )
