"""Per-turn simulation outcome guard, independent of model-authored claims."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.simulation.schemas import SimulationResult

SIMULATION_TOOLS = frozenset(
    {
        "run_simulation",
        "clarify_simulation",
        "resolve_simulation_clarification",
    }
)

_REASONS = {
    "invalid_input": "Data simulasi belum sesuai format atau batas nilai yang didukung.",
    "division_by_zero": "Rumus menghasilkan pembagian dengan nol.",
    "unknown_parameter": "Rumus merujuk parameter yang belum didefinisikan.",
    "unsupported_expression": "Rumus menggunakan operasi yang belum didukung.",
    "non_finite_result": "Perhitungan menghasilkan angka di luar batas yang didukung.",
    "input_limit": "Ukuran atau jumlah input melebihi batas simulasi.",
    "result_limit": "Hasil simulasi melebihi batas ukuran yang didukung.",
    "clarification_pending": "Masih ada klarifikasi yang perlu dijawab atau dibatalkan.",
    "session_required": "Sesi simulasi tidak dapat digunakan.",
}


@dataclass
class SimulationStatus:
    """A later fully successful calculation round may recover a prior failure.

    Mixed success/failure in one round stays blocked: successful alternatives
    cannot prove that the other requested calculations succeeded. Unrelated
    tools never clear a simulation failure. Nothing here changes saved state.
    """

    blocked: bool = False
    calculated: bool = False
    code: str | None = None
    results: list[SimulationResult] = field(default_factory=list)

    def observe(self, calls_and_results: list[tuple[str, str]]) -> None:
        failures = []
        round_results = []
        for name, raw in calls_and_results:
            if name not in SIMULATION_TOOLS:
                continue
            try:
                response = json.loads(raw)
            except (TypeError, ValueError):
                response = None
            if not isinstance(response, dict):
                failures.append(None)
                continue
            status = response.get("status")
            if not isinstance(status, str):
                failures.append(None)
                continue
            if status == "success" and name in {
                "run_simulation",
                "clarify_simulation",
                "resolve_simulation_clarification",
            }:
                try:
                    result = SimulationResult.model_validate(response.get("result"))
                except ValidationError:
                    failures.append(None)
                else:
                    round_results.append(result)
            elif (
                name == "clarify_simulation"
                and status == "needs_clarification"
                or name == "resolve_simulation_clarification"
                and status in {"selected", "cancelled", "needs_clarification"}
            ):
                continue
            else:
                code = response.get("code")
                failures.append(code if isinstance(code, str) else None)
        self.calculated = self.calculated or bool(round_results)
        if failures:
            self.blocked = True
            self.code = failures[-1]
        elif round_results:
            self.results = round_results
            self.blocked = False
            self.code = None

    def failure_reply(self, *, has_previous_result: bool, has_pending: bool) -> str:
        parts = ["Permintaan simulasi pada pesan ini belum berhasil diselesaikan."]
        parts.append(
            _REASONS.get(self.code, "Input atau proses simulasi belum dapat diproses dengan benar.")
        )
        if self.calculated:
            parts.append(
                "Ada perhitungan pada pesan ini yang berhasil dan sudah tersimpan, "
                "tetapi hasil tersebut tidak berarti seluruh permintaan berhasil."
            )
        elif has_previous_result:
            parts.append(
                "Belum ada hasil perhitungan baru yang berhasil pada pesan ini. "
                "Hasil yang tersimpan sebelumnya bukan hasil permintaan ini."
            )
        else:
            parts.append("Belum ada hasil perhitungan baru yang berhasil pada pesan ini.")
        if has_pending:
            parts.append("Jawab pilihan klarifikasi yang masih aktif, atau minta pembatalannya.")
        else:
            parts.append(
                "Mohon perbaiki atau lengkapi data, rumus, dan acuan yang dimaksud, lalu coba lagi."
            )
        return "\n\n".join(parts)
