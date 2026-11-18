import test from "node:test";
import assert from "node:assert/strict";
import {
  barScale,
  comparisonRows,
  formatSimulationNumber,
  parseSimulationReports,
} from "../src/lib/simulation.ts";

function report() {
  const input = {
    value: 10,
    origin: { kind: "synthetic", description: "Fixture" },
  };
  return {
    result: {
      definition: {
        title: "Fleet",
        objective: "Compare",
        parameters: [{ id: "fleet", label: "Fleet", unit: "trucks" }],
        outputs: [
          {
            id: "total",
            label: "Total",
            unit: "ton/day",
            expression: "fleet * 5",
          },
        ],
        assumptions: [],
        limitations: [],
      },
      baseline: { parameters: { fleet: input }, outputs: { total: 50 } },
      scenarios: [
        {
          id: "add_two",
          label: "Add two",
          parameters: { fleet: { ...input, value: 12 } },
          outputs: {
            total: {
              baselineValue: 50,
              scenarioValue: 60,
              delta: 10,
              percentageChange: 20,
            },
          },
        },
      ],
    },
  };
}

test("comparison rows retain the engine values, including undefined percentage", () => {
  const payload = report();
  payload.result.scenarios[0].outputs.total.percentageChange = null;
  const parsed = parseSimulationReports([payload]);
  assert.ok(parsed);
  const rows = comparisonRows(parsed[0], "total");
  assert.deepEqual(
    rows.map((row) => row.value),
    [50, 60],
  );
  assert.equal(rows[1].delta, 10);
  assert.equal(rows[1].percentage, null);
});

test("invalid payloads fall back rather than inventing a partial chart", () => {
  assert.equal(parseSimulationReports(undefined), null);
  assert.equal(parseSimulationReports([]), null);
  const payload = report();
  delete payload.result.scenarios[0].outputs.total;
  assert.equal(parseSimulationReports([payload]), null);
});

test("non-finite and inconsistent baselines are rejected", () => {
  const payload = report();
  payload.result.baseline.outputs.total = Infinity;
  assert.equal(parseSimulationReports([payload]), null);
  payload.result.baseline.outputs.total = 100;
  assert.equal(parseSimulationReports([payload]), null);
});

for (const values of [
  [0, 0],
  [0, 60, 75],
  [-60, -50],
  [-50, 0, 75],
  [-Number.MAX_VALUE, Number.MAX_VALUE],
  [Number.MIN_VALUE, 0],
]) {
  test(`chart geometry is finite and shares a zero origin: ${values}`, () => {
    const scale = barScale(values);
    assert.ok(
      Number.isFinite(scale.zero) && scale.zero >= 0 && scale.zero <= 100,
    );
    for (const value of values) {
      const position = scale.position(value);
      assert.ok(Number.isFinite(position) && position >= 0 && position <= 100);
      if (value < 0) assert.ok(position < scale.zero);
      if (value > 0) assert.ok(position > scale.zero);
      if (value === 0) assert.equal(position, scale.zero);
    }
  });
}

test("number labels preserve precision, zero, Indonesian separators and scientific notation", () => {
  assert.equal(
    formatSimulationNumber(0.1234567890123456),
    "0,1234567890123456",
  );
  assert.equal(formatSimulationNumber(12345.67), "12.345,67");
  assert.equal(formatSimulationNumber(-0), "0");
  assert.equal(formatSimulationNumber(-12345), "-12.345");
  assert.equal(formatSimulationNumber(1e30), "1e+30");
});

test("a scenario named baseline keeps a distinct chart identity", () => {
  const payload = report();
  payload.result.scenarios[0].id = "baseline";
  const rows = comparisonRows(parseSimulationReports([payload])[0], "total");
  assert.equal(new Set(rows.map((row) => row.id)).size, 2);
});
