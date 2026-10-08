import type { ValidationCheck, ValidationReport } from '../api/client'

const VARIANTS = ['E1', 'E2', 'E3', 'E4', 'E5', 'R_pos', 'R_neg']
const CASE_CHECKS = ['b', 'c', 'd', 'f', 'g', 'h', 'i']
const MARK = { pass: '✓', fail: '✕', pending: '…' } as const

function Cell({ checks }: { checks: ValidationCheck[] }) {
  if (checks.length === 0) return <td className="px-2 py-1 text-gray-600">—</td>
  const worst =
    checks.find((c) => c.status === 'fail') ??
    checks.find((c) => c.status === 'pending') ??
    checks[0]
  const tone =
    worst.status === 'fail'
      ? 'font-semibold text-red-800'
      : worst.status === 'pending'
        ? 'text-gray-700'
        : 'text-green-800'
  return (
    <td className={`px-2 py-1 ${tone}`} title={checks.map((c) => c.message).join(' | ')}>
      <span aria-hidden="true">{MARK[worst.status]} </span>
      {worst.status}
    </td>
  )
}

/** Validator results (plan §D.11): one row per variant, per-case checks as columns (status in
 * text, not colour alone); scenario-level checks listed with their messages. */
export function VariantGrid({ report }: { report: ValidationReport }) {
  const scenarioLevel = report.checks.filter((c) => c.case_id === null)
  const failures = report.checks.filter((c) => c.status === 'fail')
  return (
    <div>
      <p className="mb-2 text-sm">
        {report.valid ? '✓ valid' : '✕ invalid'}; {report.complete ? 'complete' : 'labels pending'}{' '}
        (token counter <span className="font-mono">{report.tokenizer}</span>)
      </p>
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse text-sm">
          <caption className="sr-only">Validator results per variant</caption>
          <thead>
            <tr>
              {['Variant', ...CASE_CHECKS.map((c) => `(${c})`)].map((h) => (
                <th key={h} scope="col" className="border-b border-gray-400 px-2 py-1 text-left">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {VARIANTS.map((v) => (
              <tr key={v} className="border-b border-gray-200">
                <th scope="row" className="px-2 py-1 text-left font-mono">
                  {v}
                </th>
                {CASE_CHECKS.map((c) => (
                  <Cell
                    key={c}
                    checks={report.checks.filter(
                      (x) => x.check === c && x.case_id === `${report.scenario_id}:${v}`,
                    )}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="mt-2 text-sm">
        {scenarioLevel.map((c) => (
          <li key={c.check}>
            <span aria-hidden="true">{MARK[c.status]} </span>
            <strong>({c.check})</strong> {c.status}: {c.message}
          </li>
        ))}
      </ul>
      {failures.length > 0 && (
        <>
          <h3 className="mt-2 text-sm font-semibold">Failures</h3>
          <ul className="list-disc pl-6 text-sm text-red-900">
            {failures.map((c, i) => (
              <li key={i}>
                ({c.check}) {c.case_id ?? 'scenario'}: {c.message}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
