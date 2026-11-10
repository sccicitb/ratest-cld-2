"""Session-scoped simulation snapshots and anchored clarification operations."""

from __future__ import annotations

import math
import uuid
from copy import deepcopy

from app.models import ChatSession
from app.simulation.engine import run_simulation
from app.simulation.schemas import (
    ParameterOverride,
    SimulationDefinition,
    SimulationScenario,
    ValueOrigin,
)


class ConversationError(ValueError):
    pass


def owned_session(ctx):
    session = ctx.db.get(ChatSession, ctx.session_id) if ctx.db and ctx.session_id else None
    if session is None or session.user_id != ctx.user_id:
        raise ConversationError("Sesi simulasi tidak ditemukan.")
    return session


def save_result(ctx, result):
    session = owned_session(ctx)
    old = session.simulation_state or {}
    if old.get("pending"):
        raise ConversationError("Jawab atau batalkan klarifikasi aktif sebelum simulasi baru.")
    session.simulation_state = {
        "revision": old.get("revision", 0) + 1,
        "result": result.model_dump(mode="json", by_alias=True),
        "pending": None,
    }
    ctx.db.commit()


def request_clarification(ctx, parameter_id: str, amount: float):
    session = owned_session(ctx)
    state = deepcopy(session.simulation_state or {})
    if state.get("pending"):
        raise ConversationError("Masih ada klarifikasi aktif; jawab atau batalkan terlebih dahulu.")
    result = state.get("result")
    if not result:
        raise ConversationError(
            "Belum ada simulasi tersimpan. Tanyakan baseline dan rumus terlebih dahulu."
        )
    definition = SimulationDefinition.model_validate(result["definition"])
    parameter = next((p for p in definition.parameters if p.id == parameter_id), None)
    if parameter is None:
        raise ConversationError("Parameter tidak dikenal; tanyakan parameter yang dimaksud.")
    last = result["scenarios"][-1]
    baseline = parameter.baseline_value
    previous = last["parameters"][parameter_id]["value"]
    options = []
    for reference, value, label in (
        ("baseline", baseline, "baseline"),
        ("last_scenario", previous, f"skenario terakhir ({last['label']})"),
    ):
        target = value + amount
        parameter.check_value(target)
        if not math.isfinite(target):
            raise ConversationError("Hasil penambahan tidak finite.")
        options.append(
            {
                "id": reference,
                "label": f"Dari {label}: {value:g} + {amount:g} → {target:g} {parameter.unit}",
                "reference": reference,
                "baseValue": value,
                "targetValue": target,
            }
        )
    pending = {
        "id": str(uuid.uuid4()),
        "revision": state["revision"],
        "parameterId": parameter_id,
        "amount": amount,
        "question": f"Penambahan {amount:g} {parameter.unit} untuk {parameter.label} dihitung dari mana?",
        "options": options,
    }
    state["pending"] = pending
    session.simulation_state = state
    ctx.db.commit()
    ctx.simulation_reply = (
        pending["question"]
        + "\n\n"
        + "\n".join(f"{i}. {option['label']}" for i, option in enumerate(options, 1))
        + "\n\nKlik pilihan atau jawab lewat chat. Kamu juga bisa mengubah jumlahnya, atau membatalkan klarifikasi."
    )
    return pending


def resolve_clarification(ctx, question_id, reference=None, amount=None, cancel=False):
    session = owned_session(ctx)
    state = deepcopy(session.simulation_state or {})
    pending = state.get("pending")
    if not pending or pending["id"] != question_id or pending["revision"] != state["revision"]:
        raise ConversationError("Pilihan sudah tidak aktif. Gunakan klarifikasi terbaru.")
    if cancel:
        state["pending"] = None
        session.simulation_state = state
        ctx.db.commit()
        ctx.simulation_reply = (
            "Klarifikasi dibatalkan. Baseline dan hasil simulasi sebelumnya tetap tersimpan."
        )
        return {"status": "cancelled"}
    option = next((o for o in pending["options"] if o["id"] == reference), None)
    if option is None:
        raise ConversationError(
            "Pilih baseline atau last_scenario; jika belum jelas, tanyakan kembali."
        )
    increment = pending["amount"] if amount is None else amount
    target = option["baseValue"] + increment
    definition = SimulationDefinition.model_validate(state["result"]["definition"])
    # A follow-up creates a new alternative, preserving the original baseline.
    # Carry all other inputs from the chosen reference, not just the edited parameter.
    source = (
        state["result"]["baseline"] if reference == "baseline" else state["result"]["scenarios"][-1]
    )
    overrides = {
        key: ParameterOverride.model_validate(value) for key, value in source["parameters"].items()
    }
    overrides[pending["parameterId"]] = ParameterOverride(
        value=target,
        origin=ValueOrigin(
            kind="user_input", description=f"Klarifikasi: {option['baseValue']:g} + {increment:g}"
        ),
    )
    definition.scenarios = [
        SimulationScenario(
            id="clarified_scenario",
            label=f"Skenario lanjutan (revisi {state['revision'] + 1})",
            overrides=overrides,
        )
    ]
    result = run_simulation(definition)
    state.update(
        revision=state["revision"] + 1,
        pending=None,
        result=result.model_dump(mode="json", by_alias=True),
    )
    session.simulation_state = state
    ctx.db.commit()
    parameter = next(p for p in definition.parameters if p.id == pending["parameterId"])
    lines = [
        (
            f"Acuan: {'baseline' if reference == 'baseline' else 'skenario terakhir'}. "
            f"{parameter.label}: {option['baseValue']:g} + {increment:g} = **{target:g} {parameter.unit}**."
        ),
        "\n| Hasil | Baseline | Skenario | Selisih | Perubahan terhadap baseline |",
        "|---|---:|---:|---:|---:|",
    ]
    for output in definition.outputs:
        value = result.scenarios[0].outputs[output.id]
        percent = (
            "Tidak terdefinisi"
            if value.percentage_change is None
            else f"{value.percentage_change:g}%"
        )
        lines.append(
            f"| {output.label} ({output.unit}) | {value.baseline_value:g} | {value.scenario_value:g} | {value.delta:g} | {percent} |"
        )
    lines.append("\nAsumsi: " + "; ".join(definition.assumptions or ["Tidak dicantumkan."]))
    lines.append(
        "\nKeterbatasan: "
        + "; ".join(
            definition.limitations
            or ["Mengikuti rumus yang diberikan; bukan validasi dampak kausal."]
        )
    )
    ctx.simulation_reply = "\n".join(lines)
    return {"status": "success", "result": state["result"]}
