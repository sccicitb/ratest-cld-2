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
