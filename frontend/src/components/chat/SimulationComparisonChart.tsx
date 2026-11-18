import { useId } from "react";
import { barScale, formatSimulationNumber } from "@/lib/simulation";
import type { ComparisonRow } from "@/lib/simulation";

export function SimulationComparisonChart({
  rows,
  unit,
  label,
}: {
  rows: ComparisonRow[];
  unit: string;
  label: string;
}) {
  const titleId = useId();
  const scale = barScale(rows.map((row) => row.value));
  return (
    <figure className="simulation-chart" aria-labelledby={titleId}>
      <figcaption id={titleId} className="sr-only">
        Perbandingan {label}, dalam {unit}. Skala dimulai dari nol dan mencakup
        nilai negatif jika ada.
      </figcaption>
      <div className="simulation-chart-rows">
        {rows.map((row, index) => {
          const end = scale.position(row.value);
          const left = Math.min(end, scale.zero);
          const width = Math.abs(end - scale.zero);
          return (
            <div className="simulation-chart-row" key={row.id}>
              <div className="simulation-chart-label">
                <span
                  className={`simulation-dot ${index === 0 ? "is-baseline" : ""}`}
                  aria-hidden="true"
                />
                <span>{row.label}</span>
                <strong>
                  {formatSimulationNumber(row.value)} <span>{unit}</span>
                </strong>
              </div>
              <svg
                viewBox="0 0 100 14"
                preserveAspectRatio="none"
                className="simulation-bar"
                aria-hidden="true"
                focusable="false"
              >
                <rect
                  x="0"
                  y="1"
                  width="100"
                  height="12"
                  rx="1.5"
                  className="simulation-bar-track"
                />
                <rect
                  x={left}
                  y="1"
                  width={width}
                  height="12"
                  rx={Math.min(1.5, width / 2)}
                  className={
                    index === 0
                      ? "simulation-bar-baseline"
                      : "simulation-bar-scenario"
                  }
                />
                <line
                  x1={scale.zero}
                  x2={scale.zero}
                  y1="0"
                  y2="14"
                  vectorEffect="non-scaling-stroke"
                  className="simulation-zero"
                />
                {row.value === 0 && (
                  <circle
                    cx={scale.zero}
                    cy="7"
                    r="1"
                    className="simulation-zero-dot"
                  />
                )}
              </svg>
            </div>
          );
        })}
      </div>
      <p className="simulation-chart-caption">
        Panjang batang menunjukkan nilai pada skala yang sama. Warna membedakan
        kondisi awal dan skenario.
      </p>
    </figure>
  );
}
