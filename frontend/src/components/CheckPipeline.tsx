import type { DecisionOut } from '../api/client'

const CHECKS = ['C1', 'C2', 'C3', 'C4', 'C5', 'C6'] as const

/** One row per gate configuration; pass/fail shown with an icon AND text (§E.4). */
export function CheckPipeline({ decisions }: { decisions: DecisionOut[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full border-collapse text-sm">
        <caption className="sr-only">Gate decisions per configuration and check</caption>
        <thead>
          <tr>
            {['System', ...CHECKS, 'Verdict', 'Correct?', 'Feedback to the agent'].map((h) => (
              <th
                key={h}
                scope="col"
                className="border-b border-gray-400 px-2 py-1 text-left font-semibold"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {decisions.map((d) => (
            <tr key={d.config_id} className="border-b border-gray-200 align-top">
              <th scope="row" className="px-2 py-1 text-left font-mono">
                {d.config_id}
              </th>
              {CHECKS.map((c) => {
                const r = d.checks.find((x) => x.check === c)
                if (!r)
                  return (
                    <td key={c} className="px-2 py-1 text-gray-600">
                      {d.config_id === 'G0' && c === 'C1' ? '—' : 'not run'}
                    </td>
                  )
                return (
                  <td
                    key={c}
                    className={`px-2 py-1 ${r.passed ? 'text-green-800' : 'font-semibold text-red-800'}`}
                    title={r.message}
                  >
                    <span aria-hidden="true">{r.passed ? '✓ ' : '✕ '}</span>
                    <span className="sr-only">{r.passed ? 'passed: ' : 'failed: '}</span>
                    <span className="font-mono">{r.code}</span>
                  </td>
                )
              })}
              <td className="px-2 py-1 font-mono">{d.verdict}</td>
              <td className="px-2 py-1">
                {d.correct === null ? (
                  '—'
                ) : d.correct ? (
                  <span className="text-green-800">✓ correct</span>
                ) : (
                  <span className="font-semibold text-red-800">✕ incorrect</span>
                )}
              </td>
              <td className="max-w-md px-2 py-1">{d.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
