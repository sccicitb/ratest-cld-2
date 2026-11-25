import { useId, useState } from "react";
import { BarChart3, ChevronDown, FlaskConical, Table2 } from "lucide-react";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";
import { SimulationComparisonChart } from "@/components/chat/SimulationComparisonChart";
import {
  comparisonRows,
  formatSimulationNumber as number,
  signedSimulationNumber as signed,
  simulationInsights,
  ORIGIN_LABELS,
} from "@/lib/simulation";
import type { SimulationReport } from "@/lib/simulation";

export function SimulationResultCard({ report }: { report: SimulationReport }) {
  const { result, referenceChange } = report;
  const { definition, baseline, scenarios } = result;
  const [outputId, setOutputId] = useState(definition.outputs[0].id);
  const [view, setView] = useState<"chart" | "table">("chart");
  const id = useId();
  const output =
    definition.outputs.find((item) => item.id === outputId) ??
    definition.outputs[0];
  const rows = comparisonRows(report, output.id);
  const insights = simulationInsights(report, output, rows);
  const kinds = new Set([
    ...Object.values(baseline.parameters).map((p) => p.origin.kind),
    ...scenarios.flatMap((s) =>
      Object.values(s.parameters).map((p) => p.origin.kind),
    ),
  ]);
  const percent = (value: number | null) =>
    value === null
      ? "Tidak terdefinisi (kondisi awal nol)"
      : `${signed(value)}%`;

  return (
    <section className="simulation-result" aria-labelledby={`${id}-title`}>
      <header className="simulation-result-header">
        <div className="simulation-eyebrow">
          <BarChart3 size={14} aria-hidden="true" /> HASIL SIMULASI
        </div>
        <h3 id={`${id}-title`}>{definition.title}</h3>
        <p>{definition.objective}</p>
        <div className="simulation-badges">
          {[...kinds].map((kind) => (
            <span key={kind}>
              <FlaskConical size={12} aria-hidden="true" />
              {ORIGIN_LABELS[kind]}
            </span>
          ))}
        </div>
      </header>

      <div className="simulation-result-body">
        {referenceChange && (
          <div className="simulation-reference">
            <ChatMarkdown>{referenceChange}</ChatMarkdown>
          </div>
        )}
        <div className="simulation-controls">
          {definition.outputs.length > 1 ? (
            <label className="simulation-indicator" htmlFor={`${id}-output`}>
              Indikator
              <select
                id={`${id}-output`}
                value={output.id}
                onChange={(e) => setOutputId(e.target.value)}
              >
                {definition.outputs.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label} ({item.unit})
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <div className="simulation-indicator">
              <span>Indikator</span>
              <strong>{output.label}</strong>
            </div>
          )}
          <div
            className="simulation-view-switch"
            role="group"
            aria-label="Tampilan perbandingan"
          >
            <button
              type="button"
              aria-pressed={view === "chart"}
              onClick={() => setView("chart")}
            >
              <BarChart3 size={14} aria-hidden="true" />
              Grafik
            </button>
            <button
              type="button"
              aria-pressed={view === "table"}
              onClick={() => setView("table")}
            >
              <Table2 size={14} aria-hidden="true" />
              Tabel
            </button>
          </div>
        </div>

        <div className="simulation-metrics">
          {rows.map((row, index) => (
            <article
              key={row.id}
              className={`simulation-metric ${index === 0 ? "is-baseline" : ""}`}
            >
              <p className="simulation-metric-label">{row.label}</p>
              <p className="simulation-metric-value">{number(row.value)}</p>
              <span className="simulation-metric-unit">{output.unit}</span>
              <p className="simulation-metric-change">
                {index === 0 ? (
                  "Acuan perbandingan"
                ) : (
                  <>
                    {signed(row.delta!)} {output.unit}
                    <br />
                    <span>{percent(row.percentage)}</span>
                  </>
                )}
              </p>
            </article>
          ))}
        </div>
        <p className="simulation-comparison-note">
          Selisih dan persentase dibandingkan dengan kondisi awal. Naik atau
          turun tidak otomatis berarti lebih baik.
        </p>

        {view === "chart" ? (
          <SimulationComparisonChart
            rows={rows}
            label={output.label}
            unit={output.unit}
          />
        ) : (
          <div
            className="simulation-table-scroll"
            tabIndex={0}
            role="region"
            aria-label={`Tabel ${output.label}`}
          >
            <table className="simulation-table">
              <caption className="sr-only">
                {output.label}, satuan {output.unit}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Skenario</th>
                  <th scope="col">Hasil ({output.unit})</th>
                  <th scope="col">Selisih ({output.unit})</th>
                  <th scope="col">Perubahan</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={row.id}>
                    <th scope="row">{row.label}</th>
                    <td>{number(row.value)}</td>
                    <td>{index === 0 ? "—" : signed(row.delta!)}</td>
                    <td>{index === 0 ? "—" : percent(row.percentage)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <section className="simulation-insights" aria-labelledby={`${id}-insights`}>
          <h4 id={`${id}-insights`}>Insight simulasi</h4>
          <dl>
            <div>
              <dt>Temuan perbandingan</dt>
              <dd>{insights.comparison}</dd>
            </div>
            <div>
              <dt>Yang perlu diperiksa</dt>
              <dd>{insights.consideration}</dd>
            </div>
          </dl>
        </section>

        <div className="simulation-context-grid">
          <section className="simulation-context">
            <h4>Asumsi</h4>
            {definition.assumptions.length ? (
              <ul>
                {definition.assumptions.map((text, i) => (
                  <li key={i}>{text}</li>
                ))}
              </ul>
            ) : (
              <p>Belum dicantumkan dalam definisi.</p>
            )}
          </section>
          <section className="simulation-context">
            <h4>Keterbatasan</h4>
            {!!definition.limitations.length && (
              <ul>
                {definition.limitations.map((text, i) => (
                  <li key={i}>{text}</li>
                ))}
              </ul>
            )}
            <p>
              Hasil mengikuti data dan rumus yang diberikan; bukan bukti dampak
              kausal kebijakan.
            </p>
          </section>
        </div>

        <details className="simulation-details">
          <summary>
            Lihat detail perhitungan{" "}
            <ChevronDown size={16} aria-hidden="true" />
          </summary>
          <div className="simulation-details-body">
            <h4>Rumus</h4>
            <dl className="simulation-formulas">
              {definition.outputs.map((item) => (
                <div key={item.id}>
                  <dt>
                    {item.label} ({item.unit})
                  </dt>
                  <dd>
                    <code>{item.expression}</code>
                  </dd>
                </div>
              ))}
            </dl>
            <h4>Parameter kondisi awal</h4>
            <div className="simulation-inputs">
              {definition.parameters.map((p) => {
                const input = baseline.parameters[p.id];
                return (
                  <div key={p.id}>
                    <strong>
                      {p.label}: {number(input.value)} {p.unit}
                    </strong>
                    <p>
                      {ORIGIN_LABELS[input.origin.kind]} ·{" "}
                      {input.origin.description}
                    </p>
                    {input.origin.reference && (
                      <p>Referensi: {input.origin.reference}</p>
                    )}
                  </div>
                );
              })}
            </div>
            {scenarios.map((s) => {
              const changed = definition.parameters.filter((p) => {
                const input = s.parameters[p.id],
                  initial = baseline.parameters[p.id];
                return (
                  input.value !== initial.value ||
                  input.origin.kind !== initial.origin.kind ||
                  input.origin.description !== initial.origin.description ||
                  input.origin.reference !== initial.origin.reference
                );
              });
              return (
                <section key={s.id} className="simulation-inputs">
                  <h4>Perubahan input: {s.label}</h4>
                  {changed.length ? (
                    changed.map((p) => {
                      const input = s.parameters[p.id];
                      return (
                        <div key={p.id}>
                          <strong>
                            {p.label}: {number(input.value)} {p.unit}
                          </strong>
                          <p>
                            {ORIGIN_LABELS[input.origin.kind]} ·{" "}
                            {input.origin.description}
                          </p>
                          {input.origin.reference && (
                            <p>Referensi: {input.origin.reference}</p>
                          )}
                        </div>
                      );
                    })
                  ) : (
                    <p>Nilai dan asal input sama dengan kondisi awal.</p>
                  )}
                </section>
              );
            })}
            <p className="simulation-comparison-note">
              Input yang tidak ditampilkan ulang mengikuti kondisi awal. Asal
              nilai adalah deklarasi input, bukan verifikasi sumber otomatis.
            </p>
          </div>
        </details>
      </div>
    </section>
  );
}
