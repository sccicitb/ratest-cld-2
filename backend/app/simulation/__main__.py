"""Manual JSON runner: python -m app.simulation path/to/scenario.json."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.simulation.engine import SimulationError, run_simulation
from app.simulation.schemas import SimulationDefinition


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a structured what-if simulation locally")
    parser.add_argument("definition", type=Path, help="Scenario definition JSON file")
    args = parser.parse_args()
    try:
        definition = SimulationDefinition.model_validate_json(args.definition.read_text("utf-8"))
        result = run_simulation(definition)
    except SimulationError as exc:
        print(
            json.dumps(
                {
                    "code": exc.code,
                    "message": str(exc),
                    "outputId": exc.output_id,
                    "scenarioId": exc.scenario_id,
                }
            ),
            file=sys.stderr,
        )
        return 2
    except (OSError, UnicodeError, ValidationError) as exc:
        print(json.dumps({"code": "invalid_input", "message": str(exc)}), file=sys.stderr)
        return 2
    print(result.model_dump_json(by_alias=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
