import { z } from "zod";

const finite = z.number().finite();
const originSchema = z.object({
  kind: z.enum(["user_input", "synthetic", "assumption", "referenced_data"]),
  description: z.string(),
  reference: z.string().nullable().optional(),
});
const inputSchema = z.object({ value: finite, origin: originSchema });
const comparisonSchema = z.object({
  baselineValue: finite,
  scenarioValue: finite,
  delta: finite,
  percentageChange: finite.nullable(),
});

export const simulationReportSchema = z
  .object({
    referenceChange: z.string().nullable().optional(),
    result: z.object({
      definition: z.object({
        title: z.string(),
        objective: z.string(),
        parameters: z.array(
          z.object({ id: z.string(), label: z.string(), unit: z.string() }),
        ),
        outputs: z
          .array(
            z.object({
              id: z.string(),
              label: z.string(),
              unit: z.string(),
              expression: z.string(),
            }),
          )
          .min(1),
        assumptions: z.array(z.string()),
        limitations: z.array(z.string()),
      }),
      baseline: z.object({
        parameters: z.record(z.string(), inputSchema),
        outputs: z.record(z.string(), finite),
      }),
      scenarios: z
        .array(
          z.object({
            id: z.string(),
            label: z.string(),
            parameters: z.record(z.string(), inputSchema),
            outputs: z.record(z.string(), comparisonSchema),
          }),
        )
        .min(1),
    }),
  })
  .superRefine(({ result }, ctx) => {
    // Do not render a partial or mismatched payload as a chart. Keep the Markdown fallback.
    for (const output of result.definition.outputs) {
      const baseline = result.baseline.outputs[output.id];
      if (
        baseline === undefined ||
        result.scenarios.some(
          (s) => s.outputs[output.id]?.baselineValue !== baseline,
        )
      ) {
        ctx.addIssue({
          code: "custom",
          message: "Missing or inconsistent output",
        });
      }
    }
    for (const parameter of result.definition.parameters) {
      if (
        !result.baseline.parameters[parameter.id] ||
        result.scenarios.some((s) => !s.parameters[parameter.id])
      ) {
        ctx.addIssue({ code: "custom", message: "Missing input" });
      }
    }
  });

export type SimulationReport = z.infer<typeof simulationReportSchema>;
type SimulationOutput =
  SimulationReport["result"]["definition"]["outputs"][number];
type ScenarioParameters = SimulationReport["result"]["baseline"]["parameters"];
export interface ComparisonRow {
  id: string;
  label: string;
  value: number;
  delta: number | null;
  percentage: number | null;
}

export function parseSimulationReports(
  value: unknown,
): SimulationReport[] | null {
  const parsed = z.array(simulationReportSchema).min(1).safeParse(value);
  return parsed.success ? parsed.data : null;
}

export function comparisonRows(
  report: SimulationReport,
  outputId: string,
): ComparisonRow[] {
  return [
    {
      id: "baseline:initial",
      label: "Kondisi awal",
      value: report.result.baseline.outputs[outputId],
      delta: null,
      percentage: null,
    },
    ...report.result.scenarios.map((s) => ({
      id: s.id,
      label: s.label,
      value: s.outputs[outputId].scenarioValue,
      delta: s.outputs[outputId].delta,
      percentage: s.outputs[outputId].percentageChange,
    })),
  ];
}

/** Normalize before finding spans: finite values can still overflow when subtracted. */
export function barScale(values: number[]) {
  const magnitude = Math.max(...values.map(Math.abs)) || 1;
  const low = Math.min(0, ...values.map((v) => v / magnitude));
  const high = Math.max(0, ...values.map((v) => v / magnitude));
  const span = high - low || 1;
  return {
    zero: low === 0 ? 0 : (-low / span) * 100,
    position: (value: number) => ((value / magnitude - low) / span) * 100,
  };
}

// Keep JS round-trip digits; localize separators without rounding calculation results.
export function formatSimulationNumber(value: number): string {
  if (value === 0) return "0";
  const text = String(value);
  if (text.includes("e")) return text;
  const [integer, fraction] = text.split(".");
  return (
    integer.replace(/\B(?=(\d{3})+(?!\d))/g, ".") +
    (fraction ? `,${fraction}` : "")
  );
}
export function signedSimulationNumber(value: number): string {
  return (value > 0 ? "+" : "") + formatSimulationNumber(value);
}
export const ORIGIN_LABELS = {
  user_input: "Input pengguna",
  synthetic: "Data sintetis",
  assumption: "Asumsi",
  referenced_data: "Data bersumber",
};

function describeInputDifference(
  definition: SimulationReport["result"]["definition"],
  from: ScenarioParameters,
  to: ScenarioParameters,
): string {
  const changed = definition.parameters.filter(
    ({ id }) => from[id].value !== to[id].value,
  );
  if (changed.length === 0) return "";

  if (changed.length === 1) {
    const { id, label, unit } = changed[0];
    const before = formatSimulationNumber(from[id].value);
    const after = formatSimulationNumber(to[id].value);
    return ` ${label} berubah dari ${before} menjadi ${after} ${unit}. Input lain tetap sama.`;
  }

  const labels = changed.slice(0, 2).map(({ label }) => label);
  if (changed.length > 2)
    labels.push(`${changed.length - 2} parameter lainnya`);
  const inputs = new Intl.ListFormat("id", { type: "conjunction" }).format(
    labels,
  );
  return ` Perbandingan ini melibatkan ${changed.length} perubahan input (${inputs}) sekaligus.`;
}

/** Describe only the selected indicator and the alternatives represented in the card. */
export function simulationInsights(
  report: SimulationReport,
  output: SimulationOutput,
  rows: ComparisonRow[],
) {
  const { definition, baseline, scenarios } = report.result;
  const [initial, ...alternatives] = rows;
  const amount = (value: number) =>
    `${formatSimulationNumber(value)} ${output.unit}`;
  const values = alternatives.map(({ value }) => value);
  const lowest = Math.min(...values);
  const highest = Math.max(...values);
  let comparison: string;

  if (alternatives.length === 1) {
    const [scenario] = alternatives;
    const label = `“${scenario.label}”`;
    if (scenario.value === initial.value) {
      comparison = `Skenario ${label} menghasilkan nilai yang sama dengan kondisi awal, yaitu ${amount(scenario.value)}.`;
    } else {
      const direction =
        scenario.value > initial.value ? "lebih tinggi" : "lebih rendah";
      comparison = `Skenario ${label} menghasilkan ${amount(scenario.value)}, ${amount(Math.abs(scenarios[0].outputs[output.id].delta))} ${direction} dari kondisi awal.`;
    }
    comparison += describeInputDifference(
      definition,
      baseline.parameters,
      scenarios[0].parameters,
    );
  } else if (lowest === highest) {
    comparison = `Seluruh ${alternatives.length} skenario menghasilkan nilai yang sama untuk indikator ini, yaitu ${amount(lowest)}.`;
  } else {
    const lowRows = alternatives.filter(({ value }) => value === lowest);
    const highRows = alternatives.filter(({ value }) => value === highest);
    const gap = highest - lowest;

    if (lowRows.length === 1 && highRows.length === 1 && Number.isFinite(gap)) {
      const [low] = lowRows;
      const [high] = highRows;
      // Avoid surfacing floating-point subtraction noise in the summary.
      const rounded = Number(gap.toPrecision(12));
      const gapLabel =
        Number.isFinite(rounded) && rounded !== gap
          ? `sekitar ${amount(rounded)}`
          : amount(gap);
      comparison = `Di antara alternatif yang diuji, hasil skenario “${high.label}” lebih tinggi ${gapLabel} daripada “${low.label}”.`;
      const lowInputs = scenarios.find(({ id }) => id === low.id)?.parameters;
      const highInputs = scenarios.find(({ id }) => id === high.id)?.parameters;
      if (lowInputs && highInputs) {
        comparison += describeInputDifference(
          definition,
          lowInputs,
          highInputs,
        );
      }
    } else {
      const describeExtreme = (matches: ComparisonRow[]) =>
        matches.length === 1
          ? `skenario “${matches[0].label}”`
          : `${matches.length} skenario`;
      comparison = `Nilai terendah adalah ${amount(lowest)} pada ${describeExtreme(lowRows)}. Nilai tertinggi adalah ${amount(highest)} pada ${describeExtreme(highRows)}.`;
    }
  }

  const limitation = definition.limitations.find((text) => text.trim())?.trim();
  const assumption = definition.assumptions.find((text) => text.trim())?.trim();
  let consideration =
    "Tetapkan target dan batas yang relevan untuk indikator ini sebelum memilih skenario. Angka perbandingan menunjukkan perubahan dalam kondisi yang disimulasikan.";
  if (limitation) {
    consideration = `Sebelum memakai hasil ini, periksa apakah batas model berikut dapat mengubah perbandingan. “${limitation}”`;
  } else if (assumption) {
    consideration = `Periksa apakah asumsi ini berlaku pada kondisi pelaksanaan. “${assumption}”`;
  }

  return { comparison, consideration };
}
