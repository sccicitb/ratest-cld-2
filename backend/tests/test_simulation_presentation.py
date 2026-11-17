"""Engine-backed display checks: numbers, scope and hostile text stay faithful."""

import json
from pathlib import Path

from app.simulation.engine import run_simulation
from app.simulation.presentation import render_reference_change, render_simulation
from app.simulation.schemas import SimulationDefinition


def definition():
    return json.loads(
        (Path(__file__).resolve().parents[1] / "examples/simulation/fleet.json").read_text()
    )


def render(data):
    return render_simulation(run_simulation(SimulationDefinition.model_validate(data)))


def test_all_scenarios_outputs_inputs_and_origins_are_shown():
    data = definition()
    data["outputs"].append(
        {
            "id": "double",
            "label": "Dua kali kapasitas",
            "unit": "ton/hari",
            "expression": "fleet * capacity * 2",
        }
    )
    text = render(data)
    assert "| Kapasitas total | ton/hari | 50 | 60 | 10 | 20% |" in text
    assert "| Kapasitas total | ton/hari | 50 | 75 | 25 | 50% |" in text
    assert "| Dua kali kapasitas | ton/hari | 100 | 150 | 50 | 50% |" in text
    assert text.count("**Skenario:") == 2
    assert "**Parameter baseline**" in text
    assert "| Jumlah armada | 12 | armada | Input pengguna:" in text
    assert "Data sintetis:" in text
    assert "Kapasitas setiap armada sama dan tenaga kerja mencukupi" in text
    assert "Belum memperhitungkan rute, downtime" in text
    assert "bukan bukti dampak kausal" in text


def test_zero_baseline_has_no_percentage_invention():
    data = definition()
    data["parameters"][0]["baselineValue"] = 0
    text = render(data)
    assert "| 0 | 60 | 60 | Tidak terdefinisi (baseline nol) |" in text
    assert "0%" not in text


def test_negative_baseline_keeps_engine_sign_convention():
    data = definition()
    data["outputs"][0]["expression"] = "-fleet * capacity"
    text = render(data)
    assert "| -50 | -60 | -10 | 20% |" in text


def test_decimal_precision_is_not_silently_rounded():
    data = definition()
    data["parameters"][1]["baselineValue"] = 0.123456789
    result = run_simulation(SimulationDefinition.model_validate(data))
    text = render_simulation(result)
    assert "0.123456789" in text
    assert str(result.scenarios[0].outputs["total"].scenario_value) in text
    assert str(result.scenarios[0].outputs["total"].percentage_change) + "%" in text


def test_metadata_cannot_inject_table_rows_or_html():
    data = definition()
    data["outputs"][0]["label"] = "Cap | forged\n| fake"
    data["parameters"][0]["origin"] = {
        "kind": "referenced_data",
        "description": "<script>bad</script>",
        "reference": "[fake](javascript:bad)",
    }
    text = render(data)
    assert "Cap \\| forged \\| fake" in text
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "\\[fake\\]" in text
    assert "\n| fake" not in text
    assert "referensi:" in text


def test_empty_assumptions_and_limits_are_explicit():
    data = definition()
    data["assumptions"] = []
    data["limitations"] = []
    text = render(data)
    assert "Tidak dicantumkan dalam definisi" in text
    assert "bukan bukti dampak kausal" in text


def test_reference_change_uses_the_same_precision_and_escaping():
    text = render_reference_change(
        reference="A|B",
        parameter="Capacity",
        unit="ton/hari",
        before=0.123456789,
        amount=0.1,
        after=0.223456789,
    )
    assert "A\\|B" in text
    assert "0.123456789 + 0.1 = **0.223456789 ton/hari**" in text
