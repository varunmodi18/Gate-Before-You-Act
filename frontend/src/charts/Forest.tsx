/** Forest plot (T7.4): one row per difference — the estimate with its 95% interval — on a scale
 * in percentage points, with the practical target or margin lines. Values arrive as proportions;
 * the axis prints pp with the unit. A text alternative lists every row. */
export interface ForestRow {
  name: string
  estimate: number // proportion difference in [-1, 1]
  ci_low: number
  ci_high: number
  target?: number // proportion (H1 0.15, H1-C4 0.10, H2 margin 0.10)
}

const W = 640
const ROW = 34
const LEFT = 190
const RIGHT = 20

export function Forest({ rows, title }: { rows: ForestRow[]; title: string }) {
  const lo = Math.min(-0.1, ...rows.map((r) => r.ci_low)) - 0.05
  const hi = Math.max(0.2, ...rows.map((r) => Math.max(r.ci_high, r.target ?? 0))) + 0.05
  const x = (v: number) => LEFT + ((v - lo) / (hi - lo)) * (W - LEFT - RIGHT)
  const h = rows.length * ROW + 40
  const ticks = [-1, -0.75, -0.5, -0.25, 0, 0.1, 0.15, 0.25, 0.5, 0.75, 1].filter(
    (t) => t >= lo && t <= hi,
  )
  const pp = (v: number) => `${(v * 100).toFixed(1)} pp`
  const alt = rows
    .map(
      (r) =>
        `${r.name}: ${pp(r.estimate)} (95% CI ${pp(r.ci_low)} to ${pp(r.ci_high)})` +
        (r.target !== undefined ? `, target or margin ${pp(r.target)}` : ''),
    )
    .join('; ')
  return (
    <figure className="mb-3">
      <figcaption className="text-sm font-semibold">{title}</figcaption>
      <svg
        role="img"
        aria-label={`${title}. ${alt}`}
        viewBox={`0 0 ${W} ${h}`}
        className="w-full max-w-3xl"
      >
        <line x1={x(0)} x2={x(0)} y1={8} y2={h - 26} stroke="#374151" strokeWidth={1.5} />
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={h - 28} y2={h - 22} stroke="#6b7280" />
            <text x={x(t)} y={h - 8} fontSize={11} textAnchor="middle" fill="#374151">
              {Math.round(t * 100)}
            </text>
          </g>
        ))}
        <text x={W - RIGHT} y={h - 8} fontSize={11} textAnchor="end" fill="#374151">
          pp
        </text>
        {rows.map((r, i) => {
          const y = 20 + i * ROW
          return (
            <g key={r.name}>
              <text x={4} y={y + 4} fontSize={12} fill="#111827">
                {r.name.length > 28 ? `${r.name.slice(0, 27)}…` : r.name}
              </text>
              {r.target !== undefined && (
                <line
                  x1={x(r.target)}
                  x2={x(r.target)}
                  y1={y - 12}
                  y2={y + 12}
                  stroke="#b45309"
                  strokeDasharray="4 3"
                  strokeWidth={2}
                />
              )}
              <line
                x1={x(r.ci_low)}
                x2={x(r.ci_high)}
                y1={y}
                y2={y}
                stroke="#1e3a8a"
                strokeWidth={2}
              />
              <rect x={x(r.estimate) - 5} y={y - 5} width={10} height={10} fill="#1e3a8a" />
            </g>
          )
        })}
      </svg>
      <p className="text-xs text-gray-700">
        Squares: estimates; lines: 95% intervals; dashed: target or margin. Scale in percentage
        points.
      </p>
    </figure>
  )
}
