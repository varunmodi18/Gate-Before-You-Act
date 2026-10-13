import { OUTCOMES } from './outcomes'

/** Stacked bars of the five outcome rates per system (T7.4). Rates are proportions; labels print
 * percentage points. A text alternative gives every value. */

const COLOURS: Record<string, string> = {
  safe_completion: '#166534',
  justified_escalation: '#1e40af',
  unnecessary_deferral: '#a16207',
  refusal: '#6b7280',
  unsafe_execution: '#b91c1c',
}

export function OutcomeBars({ data }: { data: Record<string, Record<string, number>> }) {
  const systems = Object.keys(data)
  const W = 640
  const ROW = 28
  const LEFT = 60
  const h = systems.length * ROW + 10
  const alt = systems
    .map(
      (s) =>
        `${s}: ` +
        OUTCOMES.map(
          (o) => `${o.replaceAll('_', ' ')} ${((data[s][o] ?? 0) * 100).toFixed(1)} pp`,
        ).join(', '),
    )
    .join('; ')
  return (
    <figure className="mb-3">
      <figcaption className="text-sm font-semibold">Outcomes per system</figcaption>
      <svg
        role="img"
        aria-label={`Outcomes per system. ${alt}`}
        viewBox={`0 0 ${W} ${h}`}
        className="w-full max-w-3xl"
      >
        {systems.map((s, i) => {
          let at = LEFT
          return (
            <g key={s}>
              <text x={4} y={i * ROW + 20} fontSize={12} fill="#111827">
                {s}
              </text>
              {OUTCOMES.map((o) => {
                const w = (data[s][o] ?? 0) * (W - LEFT - 10)
                const rect = (
                  <rect
                    key={o}
                    x={at}
                    y={i * ROW + 6}
                    width={w}
                    height={ROW - 10}
                    fill={COLOURS[o]}
                  />
                )
                at += w
                return rect
              })}
            </g>
          )
        })}
      </svg>
      <ul className="flex flex-wrap gap-3 text-xs" aria-label="Legend">
        {OUTCOMES.map((o) => (
          <li key={o} className="flex items-center gap-1">
            <span
              aria-hidden="true"
              className="inline-block h-3 w-3"
              style={{ background: COLOURS[o] }}
            />
            {o.replaceAll('_', ' ')}
          </li>
        ))}
      </ul>
    </figure>
  )
}
