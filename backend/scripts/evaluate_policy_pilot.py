"""Bounded synthetic chat evaluation; real loop/model, isolated DB, no other services.

Run from backend with DATABASE_URL=sqlite:///:memory:. No production DB is used.
No reasoning, credentials or real user conversations are stored in the report.
"""

from __future__ import annotations

# App imports must follow the DB isolation override below.
import argparse
import asyncio
import json
import math

# This standalone diagnostic always overrides DATABASE_URL before importing app.db.
import os
import subprocess
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chat.client import OpenAIModelClient
from app.chat.loop import run_turn
from app.chat.simulation_status import SIMULATION_TOOLS
from app.chat.turns import build_turn_registry
from app.config import settings
from app.db import Base
from app.models import ChatSession, Message, User
from app.tools.builtin.run_simulation import RunSimulation
from app.tools.context import ToolContext

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "examples/simulation/policy_pilot_cases.json"


def check_expected(expected, before, state, events, persisted):
    """Check only explicit mechanical criteria; semantic acceptance needs review."""
    supported = {
        "unchangedResult",
        "pending",
        "activeScenarioId",
        "optionTargets",
        "baseline",
        "scenarioValues",
        "percentageNull",
    }
    if not expected or set(expected) - supported:
        raise ValueError("Missing or unknown acceptance criteria")
    failures = []
    result = (state or {}).get("result")
    previous = (before or {}).get("result")
    if not any(e["type"] == "done" for e in events):
        failures.append("Turn did not complete")
    if any(e["type"] == "error" for e in events):
        failures.append("Turn emitted an error")
    if not persisted:
        failures.append("No nonempty persisted assistant answer")
    if expected.get("unchangedResult") and result != previous:
        failures.append("Calculation snapshot changed unexpectedly")
    if "pending" in expected:
        kind = ((state or {}).get("pending") or {}).get("kind")
        if kind != expected["pending"]:
            failures.append(f"Pending kind: expected {expected['pending']}, observed {kind}")
    if (
        "activeScenarioId" in expected
        and (state or {}).get("activeScenarioId") != expected["activeScenarioId"]
    ):
        failures.append("Active reference differs")
    if "optionTargets" in expected:
        observed = [
            o.get("targetValue") for o in ((state or {}).get("pending") or {}).get("options", [])
        ]
        if observed != expected["optionTargets"]:
            failures.append(f"Option targets differ: {observed}")
    if "baseline" in expected or "scenarioValues" in expected:
        if result is None:
            failures.append("No calculated result")
        else:
            # This pilot requests one output only; extra outputs need review/fail.
            baseline_values = list(result["baseline"]["outputs"].values())
            if "baseline" in expected and (
                len(baseline_values) != 1
                or not math.isclose(
                    baseline_values[0], expected["baseline"], rel_tol=1e-9, abs_tol=1e-9
                )
            ):
                failures.append(f"Baseline differs: {baseline_values}")
            values = [
                v["scenarioValue"] for s in result["scenarios"] for v in s["outputs"].values()
            ]
            wanted = expected.get("scenarioValues")
            if wanted is not None and (
                len(values) != len(wanted)
                or not all(
                    math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
                    for a, b in zip(sorted(values), sorted(wanted))
                )
            ):
                failures.append(f"Scenario outputs differ: {values}")
            if expected.get("percentageNull") and any(
                v["percentageChange"] is not None
                for s in result["scenarios"]
                for v in s["outputs"].values()
            ):
                failures.append("Zero-baseline percentage is not null")
    return failures


async def evaluate(case, fixture, timeout):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    model = OpenAIModelClient()
    trace = []
    registry = build_turn_registry()

    class ObservedRegistry:
        def schemas(self):
            return registry.schemas()

        async def execute(self, name, args, ctx):
            if name in SIMULATION_TOOLS:
                raw = await registry.execute(name, args, ctx)
            else:
                raw = json.dumps({"status": "error", "code": "pilot_external_tool_disabled"})
            try:
                response = json.loads(raw)
            except (ValueError, TypeError):
                response = {}
            trace.append(
                {
                    "tool": name,
                    "argumentKeys": list(args),
                    "status": response.get("status"),
                    "code": response.get("code"),
                    "issues": response.get("issues", []),
                }
            )
            return raw

    record = {
        "id": case["id"],
        "name": case["name"],
        "review": case["review"],
        "manualReview": "pending",
        "steps": [],
    }
    try:
        with factory() as db:
            db.add(
                User(
                    id="pilot-user",
                    email="synthetic@example.invalid",
                    display_name="Pilot",
                    password_hash="unused",
                )
            )
            db.flush()
            db.add(ChatSession(id="pilot-session", user_id="pilot-user", title="Synthetic pilot"))
            db.commit()
            if case.get("seedScenarioIds"):
                definition = deepcopy(fixture)
                definition["scenarios"] = [
                    s for s in definition["scenarios"] if s["id"] in case["seedScenarioIds"]
                ]
                ctx = ToolContext(
                    user_id="pilot-user",
                    session_id="pilot-session",
                    db=db,
                    client=None,
                    embedder=None,
                )
                response = json.loads(await RunSimulation().execute(definition, ctx))
                if response["status"] != "success":
                    raise ValueError("Invalid seed fixture")
        for item in case["steps"]:
            trace.clear()
            started = time.monotonic()
            # Open a fresh SQLAlchemy session each turn: no in-process ctx carryover.
            with factory() as db:
                room = db.get(ChatSession, "pilot-session")
                before = deepcopy(room.simulation_state)
                ctx = ToolContext(
                    user_id="pilot-user", session_id=room.id, db=db, client=None, embedder=None
                )

                async def consume(room=room, message=item["message"], ctx=ctx):
                    return [
                        e
                        async for e in run_turn(
                            db=db,
                            session=room,
                            message=message,
                            registry=ObservedRegistry(),
                            model=model,
                            ctx=ctx,
                        )
                    ]

                try:
                    events = await asyncio.wait_for(consume(), timeout)
                except asyncio.TimeoutError:
                    record["steps"].append(
                        {
                            "message": item["message"],
                            "failures": ["Turn timed out"],
                            "tools": deepcopy(trace),
                        }
                    )
                    break
                after = deepcopy(room.simulation_state)
                done_event = next((e for e in reversed(events) if e["type"] == "done"), None)
                saved = db.get(Message, done_event["messageId"]).content if done_event else ""
                failures = check_expected(item["expect"], before, after, events, saved)
                record["steps"].append(
                    {
                        "message": item["message"],
                        "expect": item["expect"],
                        "seconds": round(time.monotonic() - started, 2),
                        "tools": deepcopy(trace),
                        "answer": saved,
                        "state": after,
                        "failures": failures,
                    }
                )
                if failures:
                    break
        record["automaticChecks"] = (
            "pass"
            if len(record["steps"]) == len(case["steps"])
            and all(not s["failures"] for s in record["steps"])
            else "fail"
        )
    except Exception as exc:  # noqa: BLE001 - record one failed case without exposing service details
        # Do not serialize service exception text: it may contain connection data.
        record["automaticChecks"] = "error"
        record["errorType"] = type(exc).__name__
    finally:
        await model._client.close()
        engine.dispose()
    return record


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly allow calls to MODEL_BASE_URL"
    )
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("/tmp/citya-policy-pilot.json"))
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Timeout must be positive and finite")
    corpus = json.loads(CORPUS.read_text())
    cases = [c for c in corpus["cases"] if not args.case or c["id"] in args.case]
    if not cases or set(args.case) - {c["id"] for c in corpus["cases"]}:
        parser.error("Unknown or empty case selection")
    if not args.live:
        print(json.dumps({"cases": [c["id"] for c in cases], "networkCalls": False}))
        return
    report = {
        "startedAtUtc": datetime.now(timezone.utc).isoformat(),
        "model": settings.model_name,
        "commit": (
            await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT)
        )
        .decode()
        .strip(),
        "workingTree": (
            await asyncio.to_thread(subprocess.check_output, ["git", "status", "--short"], cwd=ROOT)
        ).decode(),
        "limitations": [
            "Synthetic only",
            "No real KB/MCP/sandbox/artifact services",
            "DB session reopen is not browser reload",
            "Manual/domain review still required",
        ],
        "cases": [],
    }
    fixture = json.loads((CORPUS.parent / corpus["fixture"]).read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        print("START", case["id"], flush=True)
        record = await evaluate(case, fixture, args.timeout)
        report["cases"].append(record)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(
            case["id"],
            record["automaticChecks"],
            [s["failures"] for s in record["steps"]],
            flush=True,
        )
    print("REPORT", str(args.output), flush=True)
    return 1 if any(c["automaticChecks"] != "pass" for c in report["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
