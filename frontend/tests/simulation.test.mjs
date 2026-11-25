import test from "node:test";
import assert from "node:assert/strict";
import {
  barScale,
  comparisonRows,
  formatSimulationNumber,
  parseSimulationReports,
  simulationInsights,
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

function insight(payload, outputId = "total") {
  const parsed = parseSimulationReports([payload]);
  assert.ok(parsed);
  const [item] = parsed;
  const output = item.result.definition.outputs.find(
    ({ id }) => id === outputId,
  );
  assert.ok(output);
  return simulationInsights(item, output, comparisonRows(item, outputId));
}

test("insight compares alternatives directly and follows the selected output", () => {
  const payload = report();
  const { result } = payload;
  const [first] = result.scenarios;
  const limitation = "Kapasitas rute dan fasilitas belum dimodelkan.";
  result.definition.limitations = [limitation];
  const second = structuredClone(first);
  second.id = "add_five";
  second.label = "Add five";
  second.parameters.fleet.value = 15;
  second.outputs.total = {
    baselineValue: 50,
    scenarioValue: 75,
    delta: 25,
    percentageChange: 50,
  };
  result.scenarios.push(second);
  result.definition.outputs.push({
    id: "cost",
    label: "Biaya",
    unit: "rupiah",
    expression: "fleet * 100",
  });
  result.baseline.outputs.cost = 1000;
  first.outputs.cost = {
    baselineValue: 1000,
    scenarioValue: 1200,
    delta: 200,
    percentageChange: 20,
  };
  second.outputs.cost = {
    baselineValue: 1000,
    scenarioValue: 1500,
    delta: 500,
    percentageChange: 50,
  };

  const total = insight(payload);
  assert.match(
    total.comparison,
    /“Add five” lebih tinggi 15 ton\/day daripada “Add two”/,
  );
  assert.match(total.comparison, /Fleet berubah dari 12 menjadi 15 trucks/);
  assert.ok(total.consideration.includes(limitation));
  assert.match(insight(payload, "cost").comparison, /lebih tinggi 300 rupiah/);

  second.outputs.total = { ...first.outputs.total };
  assert.match(
    insight(payload).comparison,
    /Seluruh 2 skenario menghasilkan nilai yang sama/,
  );
  const third = structuredClone(second);
  third.id = "different";
  third.outputs.total = {
    baselineValue: 50,
    scenarioValue: 75,
    delta: 25,
    percentageChange: 50,
  };
  result.scenarios.push(third);
  assert.match(insight(payload).comparison, /60 ton\/day pada 2 skenario/);
});

test("one-scenario insight uses absolute direction with negative or zero baselines", () => {
  const payload = report();
  const { result } = payload;
  const [scenario] = result.scenarios;
  result.baseline.outputs.total = -50;
  scenario.outputs.total = {
    baselineValue: -50,
    scenarioValue: -60,
    delta: -10,
    percentageChange: 20,
  };
  assert.match(
    insight(payload).comparison,
    /10 ton\/day lebih rendah dari kondisi awal/,
  );

  result.baseline.outputs.total = 0;
  scenario.outputs.total = {
    baselineValue: 0,
    scenarioValue: 60,
    delta: 60,
    percentageChange: null,
  };
  assert.match(
    insight(payload).comparison,
    /60 ton\/day lebih tinggi dari kondisi awal/,
  );
  assert.doesNotMatch(insight(payload).comparison, /%/);

  scenario.outputs.total = {
    baselineValue: 0,
    scenarioValue: 0,
    delta: 0,
    percentageChange: null,
  };
  assert.match(
    insight(payload).comparison,
    /nilai yang sama dengan kondisi awal/,
  );
});

test("insight describes joint input changes without attributing the result to one parameter", () => {
  const payload = report();
  const { result } = payload;
  result.definition.parameters.push({
    id: "capacity",
    unit: "ton/truck",
    label: "Capacity",
  });
  const input = result.baseline.parameters.fleet;
  result.baseline.parameters.capacity = { ...input, value: 5 };
  result.scenarios[0].parameters.capacity = { ...input, value: 6 };
  result.scenarios[0].outputs.total = {
    baselineValue: 50,
    scenarioValue: 72,
    delta: 22,
    percentageChange: 44,
  };
  result.definition.assumptions = ["Jam kerja tetap."];

  assert.match(
    insight(payload).comparison,
    /2 perubahan input \(Fleet dan Capacity\) sekaligus/,
  );
  assert.match(insight(payload).consideration, /Jam kerja tetap/);
});
