"""Session-scoped simulation snapshots and anchored clarification operations."""

from __future__ import annotations

import math
import uuid
from copy import deepcopy

from app.models import ChatSession
from app.simulation.engine import run_simulation
from app.simulation.presentation import render_reference_change, render_simulation
from app.simulation.schemas import (
    ParameterOverride,
    SimulationDefinition,
    SimulationReport,
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
    state = {
        "revision": old.get("revision", 0) + 1,
        "result": result.model_dump(mode="json", by_alias=True),
        "pending": None,
        "activeScenarioId": result.scenarios[0].id if len(result.scenarios) == 1 else None,
    }
    session.simulation_state = state
    ctx.db.commit()


def active_scenario(state):
    """Resolve identity, never list order; only a sole legacy scenario is unambiguous."""
    scenarios = state.get("result", {}).get("scenarios", [])
    scenario_id = state.get("activeScenarioId")
    if scenario_id:
        return next((item for item in scenarios if item["id"] == scenario_id), None)
    return scenarios[0] if len(scenarios) == 1 else None


def _new_pending(state, question, options, **details):
    return {
        "id": str(uuid.uuid4()),
        "revision": state["revision"],
        "question": question,
        "options": options,
        **details,
    }


def _publish_pending(ctx, session, state, question, options, **details):
    pending = _new_pending(state, question, options, **details)
    state["pending"] = pending
    session.simulation_state = state
    ctx.db.commit()
    ctx.simulation_reply = (
        question
        + "\n\n"
        + "\n".join(f"{i}. {option['label']}" for i, option in enumerate(options, 1))
        + "\n\nKlik pilihan atau jawab lewat chat. Kamu juga bisa membatalkan klarifikasi."
    )
    return pending


def _scenario_options(state):
    parameters = {p["id"]: p for p in state["result"]["definition"]["parameters"]}
    return [
        {
            "id": f"scenario:{item['id']}",
            "scenarioId": item["id"],
            "label": f"{item['label']} ("
            + ", ".join(
                f"{parameters[key]['label']} = {value['value']:g} {parameters[key]['unit']}"
                for key, value in item["parameters"].items()
            )
            + ")",
        }
        for item in state["result"]["scenarios"]
    ]


def request_clarification(
    ctx, parameter_id: str | None = None, amount: float | None = None, reference: str | None = None
):
    session = owned_session(ctx)
    return _request_clarification(
        ctx, session, deepcopy(session.simulation_state or {}), parameter_id, amount, reference
    )


def _request_clarification(ctx, session, state, parameter_id, amount, reference=None):
    if state.get("pending"):
        raise ConversationError("Masih ada klarifikasi aktif; jawab atau batalkan terlebih dahulu.")
    if not state.get("result"):
        raise ConversationError(
            "Belum ada simulasi tersimpan. Tanyakan baseline dan rumus terlebih dahulu."
        )
    if parameter_id is None and amount is None:
        if reference is not None:
            raise ConversationError(
                "Sebutkan parameter dan penambahan sebelum menghitung dari acuan eksplisit."
            )
        return _publish_pending(
            ctx,
            session,
            state,
            "Skenario mana yang ingin kamu lanjutkan?",
            _scenario_options(state),
            kind="select_scenario",
        )
    if parameter_id is None or amount is None or not math.isfinite(amount):
        raise ConversationError("Parameter dan jumlah penambahan harus lengkap dan finite.")
    definition = SimulationDefinition.model_validate(state["result"]["definition"])
    parameter = next((p for p in definition.parameters if p.id == parameter_id), None)
    if parameter is None:
        raise ConversationError("Parameter tidak dikenal; tanyakan parameter yang dimaksud.")
    active = active_scenario(state)
    if reference is not None:
        if reference == "baseline":
            source = state["result"]["baseline"]
        elif reference == "last_scenario":
            source = active
        else:
            scenario_id = reference.removeprefix("scenario:")
            source = next(
                (item for item in state["result"]["scenarios"] if item["id"] == scenario_id), None
            )
        if source is None:
            raise ConversationError(
                "Acuan belum jelas atau tidak ditemukan; minta user memilih skenario."
            )
        return _calculate_change(ctx, session, state, parameter_id, amount, reference, source)
    # Without a selected identity, offer every alternative; none is the default.
    scenarios = [active] if active else state["result"]["scenarios"]
    sources = [("baseline", None, parameter.baseline_value, "baseline")]
    sources.extend(
        (
            "last_scenario" if active else f"scenario:{item['id']}",
            item["id"],
            item["parameters"][parameter_id]["value"],
            f"skenario {item['label']}",
        )
        for item in scenarios
    )
    options = []
    for option_id, scenario_id, value, label in sources:
        target = value + amount
        parameter.check_value(target)
        if not math.isfinite(target):
            raise ConversationError("Hasil penambahan tidak finite.")
        options.append(
            {
                "id": option_id,
                "scenarioId": scenario_id,
                "label": f"Dari {label}: {value:g} + {amount:g} → {target:g} {parameter.unit}",
                "baseValue": value,
                "targetValue": target,
            }
        )
    return _publish_pending(
        ctx,
        session,
        state,
        f"Penambahan {amount:g} {parameter.unit} untuk {parameter.label} dihitung dari mana?",
        options,
        kind="add",
        parameterId=parameter_id,
        amount=amount,
    )


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
            "Pilih ID opsi yang tersedia pada klarifikasi aktif; jika belum jelas, tanyakan kembali."
        )
    scenario_id = option.get("scenarioId")
    if reference != "baseline" and "scenarioId" not in option:
        # Old pending options were anchored only by list position. Reconfirm all
        # alternatives instead of silently trusting that historic interpretation.
        legacy_scenarios = state["result"]["scenarios"]
        legacy_active = legacy_scenarios[0] if len(legacy_scenarios) == 1 else None
        if legacy_active is None:
            state["pending"] = None
            replacement = _request_clarification(
                ctx, session, state, pending["parameterId"], pending["amount"]
            )
            return {"status": "needs_clarification", "pending": replacement}
        scenario_id = legacy_active["id"]
    source = (
        state["result"]["baseline"]
        if reference == "baseline"
        else next(
            (item for item in state["result"]["scenarios"] if item["id"] == scenario_id), None
        )
    )
    if source is None:
        raise ConversationError("Skenario acuan tidak ditemukan. Batalkan dan pilih ulang.")
    if pending.get("kind") == "select_scenario":
        if amount is not None:
            raise ConversationError(
                "Pemilihan acuan tidak menghitung penambahan. Kirim perubahan setelah memilih."
            )
        state.update(activeScenarioId=scenario_id, pending=None)
        session.simulation_state = state
        ctx.db.commit()
        ctx.simulation_reply = (
            f"Acuan aktif dipilih: **{source['label']}**. Hasil dan baseline tidak berubah."
        )
        return {"status": "selected", "activeScenarioId": scenario_id}
    increment = pending["amount"] if amount is None else amount
    return _calculate_change(
        ctx, session, state, pending["parameterId"], increment, reference, source
    )


def _calculate_change(ctx, session, state, parameter_id, increment, reference, source):
    target = source["parameters"][parameter_id]["value"] + increment
    definition = SimulationDefinition.model_validate(state["result"]["definition"])
    # Carry every input from the identified reference; the baseline stays intact.
    overrides = {
        key: ParameterOverride.model_validate(value) for key, value in source["parameters"].items()
    }
    overrides[parameter_id] = ParameterOverride(
        value=target,
        origin=ValueOrigin(
            kind="user_input",
            description=f"Perubahan lanjutan: {source['parameters'][parameter_id]['value']:g} + {increment:g}",
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
        activeScenarioId=result.scenarios[0].id,
        result=result.model_dump(mode="json", by_alias=True),
    )
    session.simulation_state = state
    ctx.db.commit()
    parameter = next(p for p in definition.parameters if p.id == parameter_id)
    reference_change = render_reference_change(
        reference="kondisi awal" if reference == "baseline" else source["label"],
        parameter=parameter.label,
        unit=parameter.unit,
        before=source["parameters"][parameter_id]["value"],
        amount=increment,
        after=target,
    )
    ctx.simulation_report = SimulationReport(result=result, reference_change=reference_change)
    ctx.simulation_reply = reference_change + "\n\n" + render_simulation(result)
    return {"status": "success", "result": state["result"]}
