"""Negative controls for the pilot rubric; no duplicate engine or LLM tests."""

import json
from pathlib import Path

import pytest

from scripts.evaluate_policy_pilot import check_expected

DONE = [{"type": "done", "messageId": "m"}]


def snapshot(baseline=50, output=60, percent=20):
    return {
        "result": {
            "baseline": {"outputs": {"total": baseline}},
            "scenarios": [
                {"outputs": {"total": {"scenarioValue": output, "percentageChange": percent}}}
            ],
        }
    }


def test_wrong_reference_fails_even_with_a_plausible_answer():
    failures = check_expected(
        {"activeScenarioId": "requested"},
        None,
        {"activeScenarioId": "comparison"},
        DONE,
        "Hasil benar",
    )
    assert "Active reference differs" in failures


def test_invented_result_does_not_pass_an_unchanged_requirement():
    assert check_expected({"unchangedResult": True}, None, snapshot(), DONE, "Jawaban")


def test_extra_output_cannot_pass_numeric_criterion():
    state = snapshot()
    state["result"]["scenarios"].append({"outputs": {"extra": {"scenarioValue": 60}}})
    assert check_expected({"baseline": 50, "scenarioValues": [60]}, None, state, DONE, "Jawaban")


def test_zero_percent_is_not_equivalent_to_undefined():
    assert check_expected(
        {"baseline": 0, "scenarioValues": [10], "percentageNull": True},
        None,
        snapshot(0, 10, 0),
        DONE,
        "Jawaban",
    )
    assert not check_expected(
        {"baseline": 0, "scenarioValues": [10], "percentageNull": True},
        None,
        snapshot(0, 10, None),
        DONE,
        "Jawaban",
    )


def test_no_completion_or_empty_answer_cannot_pass():
    assert check_expected({"unchangedResult": True}, None, None, [], "")


def test_typo_in_rubric_cannot_silently_pass():
    with pytest.raises(ValueError):
        check_expected({"baseine": 50}, None, snapshot(), DONE, "Jawaban")


def test_corpus_references_existing_fixture_scenarios_and_unique_cases():
    path = Path(__file__).resolve().parents[1] / "examples/simulation/policy_pilot_cases.json"
    corpus = json.loads(path.read_text())
    fixture = json.loads((path.parent / corpus["fixture"]).read_text())
    ids = [case["id"] for case in corpus["cases"]]
    assert len(set(ids)) == len(ids)
    known = {scenario["id"] for scenario in fixture["scenarios"]}
    for case in corpus["cases"]:
        assert set(case.get("seedScenarioIds", [])) <= known
        assert case["review"]
        for step in case["steps"]:
            assert step["message"] and step["expect"]
            # Unknown rubric keys raise; missing expected results may fail normally.
            check_expected(step["expect"], None, None, [], "")
