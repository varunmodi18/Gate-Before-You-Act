import type { DecisionOut, ReferenceHit } from '../api/client'

const SCOPE = ['host', 'account', 'command', 'time'] as const

/** C4 output for one gate configuration (plan §E.1 row 5): verdict, unmet requirement, ticket
 * scope, reason, the retrieved references (gold rules marked, for the analyst only) and the exact
 * verifier prompt. Retrieved Sigma rules show title, author and a link (DRL 1.1 attribution). */
export function VerifierPanel({
  decision,
  verifierLabel,
}: {
  decision: DecisionOut
  verifierLabel: string | null
}) {
  const vc = decision.verifier_call
  if (!vc) return null
  const out = vc.output
  const headingId = `vp-${decision.config_id}`
  const sigma = vc.references.filter((r) => r.kind === 'sigma')
  const attack = vc.references.filter((r) => r.kind === 'attack')
  return (
    <section aria-labelledby={headingId} className="mt-4 rounded border border-gray-400 p-3">
      <h2 id={headingId} className="mb-2 text-lg font-semibold">
        Verifier for <span className="font-mono">{decision.config_id}</span> (variant{' '}
        <span className="font-mono">{vc.variant}</span>)
      </h2>
      {out ? (
        <dl className="grid max-w-4xl grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="font-semibold">Verdict</dt>
          <dd>
            <span className="font-mono font-semibold">{out.verdict}</span>
            {verifierLabel && (
              <span className="ml-2">
                (label <span className="font-mono">{verifierLabel}</span>:{' '}
                {verifierLabel === out.verdict ? (
                  <span className="text-green-800">✓ matches</span>
                ) : (
                  <span className="font-semibold text-red-800">✕ differs</span>
                )}
                )
              </span>
            )}
          </dd>
          <dt className="font-semibold">Unmet requirement</dt>
          <dd>{out.unmet_requirement ?? '—'}</dd>
          <dt className="font-semibold">Ticket scope</dt>
          <dd>
            {out.ticket_scope ? (
              <>
                applies: <strong>{out.ticket_scope.applies ? 'yes' : 'no'}</strong>;{' '}
                {SCOPE.map((k) => (
                  <span key={k} className="mr-2">
                    {k}: {out.ticket_scope?.matches[k] ? '✓ match' : '✕ no match'}
                  </span>
                ))}
              </>
            ) : (
              '—'
            )}
          </dd>
          <dt className="font-semibold">Reason</dt>
          <dd>{out.reason}</dd>
        </dl>
      ) : (
        <p className="text-sm font-semibold text-red-800">
          Output could not be parsed (C4_PARSE_ERROR): {vc.error}
        </p>
      )}
      <p className="mt-1 text-xs text-gray-700">
        {vc.tokens_in} prompt / {vc.tokens_out} output tokens; {Math.round(vc.ms)} ms; prompt hash{' '}
        <span className="font-mono">{vc.prompt_hash?.slice(0, 12)}</span>
      </p>
      {vc.references.length > 0 && (
        <References sigma={sigma} attack={attack} caption={`Retrieved for ${decision.config_id}`} />
      )}
      {vc.messages && (
        <details className="mt-2">
          <summary className="cursor-pointer text-sm font-semibold">Exact verifier prompt</summary>
          {vc.messages.map((m) => (
            <div key={m.role} className="mt-2">
              <h3 className="text-sm font-semibold">{m.role}</h3>
              <pre
                tabIndex={0}
                aria-label={`${m.role} prompt (scrollable)`}
                className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-100 p-2 text-xs"
              >
                {m.content}
              </pre>
            </div>
          ))}
        </details>
      )}
    </section>
  )
}

function References({
  sigma,
  attack,
  caption,
}: {
  sigma: ReferenceHit[]
  attack: ReferenceHit[]
  caption: string
}) {
  const rows = [...sigma, ...attack]
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="min-w-full border-collapse text-sm">
        <caption className="text-left text-sm font-semibold">
          {caption}: Sigma top {sigma.length} and ATT&amp;CK top {attack.length} (the verifier sees
          Sigma 1–5 and ATT&amp;CK 1; ★ marks the case&apos;s gold set, which the verifier never
          sees)
        </caption>
        <thead>
          <tr>
            {['Source', 'Rank', 'Shown', 'Gold', 'Title', 'Author', 'BM25', 'Rerank'].map((h) => (
              <th key={h} scope="col" className="border-b border-gray-400 px-2 py-1 text-left">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.kind}-${r.doc_id}`} className="border-b border-gray-200">
              <td className="px-2 py-1">{r.kind === 'sigma' ? 'Sigma' : 'ATT&CK'}</td>
              <td className="px-2 py-1">{r.rank}</td>
              <td className="px-2 py-1">{r.shown ? 'shown' : '—'}</td>
              <td className="px-2 py-1">{r.gold ? '★ gold' : '—'}</td>
              <td className="px-2 py-1">
                {r.uri ? (
                  <a
                    className="text-blue-800 underline"
                    href={r.uri}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {r.title}
                  </a>
                ) : (
                  r.title
                )}
                {r.technique_id && <span className="ml-1 font-mono">({r.technique_id})</span>}
              </td>
              <td className="px-2 py-1">{r.author ?? '—'}</td>
              <td className="px-2 py-1 font-mono">{r.bm25.toFixed(2)}</td>
              <td className="px-2 py-1 font-mono">
                {r.rerank === null ? '—' : r.rerank.toFixed(3)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
