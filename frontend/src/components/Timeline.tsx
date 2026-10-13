import { Fragment } from 'react'

import type { CheckResult, StepOut } from '../api/client'
import { isInvestigation, recoveryState } from './recovery'

type P = Record<string, unknown>
const str = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v))

export function RecoveryMeter({ steps }: { steps: StepOut[] }) {
  const r = recoveryState(steps)
  if (r.granted === null)
    return <p className="text-sm">Recovery budget: not granted yet (no INSUFFICIENT verdict).</p>
  return (
    <div className="text-sm">
      <label htmlFor="recovery-meter">
        Recovery budget: {Math.min(r.used, r.granted)} of {r.granted} queries used
        {r.refused > 0 && `; ${r.refused} refused (budget exhausted)`}
      </label>
      <meter
        id="recovery-meter"
        className="ml-2 align-middle"
        min={0}
        max={Math.max(r.granted, 1)}
        value={Math.min(r.used, r.granted)}
      />
    </div>
  )
}

function Chips({ checks }: { checks: CheckResult[] }) {
  return (
    <ul className="mt-1 flex flex-wrap gap-1" aria-label="Checks">
      {checks.map((c) => (
        <li
          key={c.check}
          className={`rounded border px-1 font-mono text-xs ${
            c.passed ? 'border-green-700 text-green-900' : 'border-red-700 text-red-900'
          }`}
        >
          {c.passed ? '✓' : '✕'} {c.check} {c.code}
        </li>
      ))}
    </ul>
  )
}

function Card({
  title,
  tone,
  children,
}: {
  title: string
  tone: string
  children: React.ReactNode
}) {
  return (
    <li className={`rounded border-l-4 ${tone} border-y border-r border-gray-300 p-2`}>
      <p className="text-sm font-semibold">{title}</p>
      <div className="text-sm">{children}</div>
    </li>
  )
}

const VERDICT_TONE: Record<string, string> = {
  ACCEPT: 'border-l-green-700',
  REJECT: 'border-l-red-700',
}

/** Steps of the live investigation (Exp 3, Console window mode; ``phase: investigation``). */
function InvestigationCard({ step }: { step: StepOut }) {
  const p = step.payload
  if (step.kind === 'tool') {
    const profile = p.tool === 'profile'
    const content = typeof p.content === 'string' ? p.content : ''
    return (
      <Card
        title={
          profile
            ? 'Investigation: window profile'
            : `Investigation query for ${str(p.technique_id)}${p.ok ? '' : ' (refused)'}`
        }
        tone="border-l-violet-700"
      >
        <span className="mr-2 rounded bg-amber-100 px-1 text-xs font-semibold text-amber-900">
          untrusted log data
        </span>
        {!profile && <p className="font-mono text-xs break-all">{str((p.args as P)?.sql)}</p>}
        {content && (
          <pre
            tabIndex={0}
            aria-label={`Investigation result, step ${step.idx}`}
            className="mt-1 max-h-48 overflow-auto bg-gray-50 p-1 text-xs whitespace-pre-wrap"
          >
            {content}
          </pre>
        )}
      </Card>
    )
  }
  const out = p.output as P | null
  if (!out)
    return (
      <Card title={`Investigation: ${str(p.step)} output unusable`} tone="border-l-red-700">
        <p>{str(p.error)}</p>
      </Card>
    )
  if (p.step === 'hypothesise') {
    const hs = (out.hypotheses ?? []) as P[]
    return (
      <Card title={`Investigation: ${hs.length} hypotheses`} tone="border-l-violet-700">
        {out.thought ? <p className="italic">{str(out.thought)}</p> : null}
        <ul className="list-disc pl-5">
          {hs.map((h, i) => (
            <li key={i}>
              <span className="font-mono">{str(h.technique_id)}</span>: {str(h.behaviour)}
            </li>
          ))}
        </ul>
      </Card>
    )
  }
  if (p.step === 'sqlgen') {
    const qs = (out.queries ?? []) as P[]
    return (
      <Card title={`Investigation: ${qs.length} queries proposed`} tone="border-l-violet-700">
        {qs.map((q, i) => (
          <p key={i} className="font-mono text-xs break-all">
            {str(q.sql)}
          </p>
        ))}
      </Card>
    )
  }
  const verdict = str(out.verdict)
  const cited = (out.cited ?? []) as number[]
  return (
    <Card
      title={`Technique verifier: ${verdict}`}
      tone={VERDICT_TONE[verdict] ?? 'border-l-gray-500'}
    >
      <p>{str(out.reason)}</p>
      {cited.length > 0 && <p className="font-mono text-xs">cited [{cited.join(', ')}]</p>}
    </Card>
  )
}

function StepCard({ step }: { step: StepOut }) {
  const p = step.payload
  if (p.phase === 'investigation') return <InvestigationCard step={step} />
  if (step.kind === 'llm') {
    const prop = p.proposal as P | null
    if (!prop)
      return (
        <Card title={`Model output ${str(p.call)} unusable`} tone="border-l-red-700">
          <p>{str(p.error)}</p>
        </Card>
      )
    return (
      <Card
        title={`Proposal ${str(p.call)}${p.reask ? ' (re-ask)' : ''}: ${str(prop.tool)}`}
        tone="border-l-blue-800"
      >
        {prop.thought ? <p className="italic">{str(prop.thought)}</p> : null}
        <p className="font-mono text-xs break-all">args {JSON.stringify(prop.args)}</p>
        {Array.isArray(prop.cited) && prop.cited.length > 0 && (
          <p className="font-mono text-xs">cited [{(prop.cited as number[]).join(', ')}]</p>
        )}
        {prop.technique_id ? (
          <p className="text-xs">claimed technique {str(prop.technique_id)}</p>
        ) : null}
      </Card>
    )
  }
  if (step.kind === 'gate') {
    const d = (p.decision ?? {}) as P
    const verdict = str(d.verdict)
    const ok = verdict === 'admitted'
    return (
      <Card
        title={`Gate ${str(d.config_id)}: ${verdict} (${str(d.tool)})`}
        tone={ok ? 'border-l-green-700' : 'border-l-red-700'}
      >
        <Chips checks={(d.checks ?? []) as CheckResult[]} />
      </Card>
    )
  }
  if (step.kind === 'approval')
    return (
      <Card
        title={`Approval request (${p.via === 'c6' ? 'converted by C6' : 'explicit'}): ${str(p.code)}`}
        tone="border-l-amber-600"
      >
        <p>{str(p.message)}</p>
        <p className="text-xs">state after: {str(p.state)}</p>
      </Card>
    )
  if (step.kind === 'feedback')
    return (
      <Card title="Feedback to the agent" tone="border-l-gray-500">
        <p className="break-words">{str(p.text)}</p>
      </Card>
    )
  // tool
  if (p.control)
    return (
      <Card title={`end_episode (${str(p.reason)})`} tone="border-l-gray-700">
        <p>Control action: no tool and no gate check.</p>
      </Card>
    )
  const untrusted = p.tool === 'sql_query' && Boolean(p.ok)
  const content = typeof p.content === 'string' ? p.content : ''
  return (
    <Card title={`Tool result: ${str(p.tool)}`} tone="border-l-gray-400">
      {untrusted && (
        <span className="mr-2 rounded bg-amber-100 px-1 text-xs font-semibold text-amber-900">
          untrusted log data
        </span>
      )}
      {p.error_code ? <p className="font-mono text-xs">{str(p.error_code)}</p> : null}
      {p.escalation ? (
        <p>Handed off: {str((p.escalation as P).message)}</p>
      ) : (
        content && (
          <pre
            tabIndex={0}
            aria-label={`Result of ${str(p.tool)}, step ${step.idx}`}
            className="mt-1 max-h-48 overflow-auto bg-gray-50 p-1 text-xs whitespace-pre-wrap"
          >
            {content}
          </pre>
        )
      )}
      {p.approval_state ? (
        <p className="text-xs">approval state at execution: {str(p.approval_state)}</p>
      ) : null}
    </Card>
  )
}

/** The episode as a timeline (Console and Traces): proposals, gate cards, tool results, approval
 * events and feedback, in trace order. An Exp 3 episode starts with its live investigation; a
 * heading marks where each phase begins. */
export function Timeline({ steps }: { steps: StepOut[] }) {
  const firstResponse = steps.findIndex((s) => !isInvestigation(s))
  const live = steps.length > 0 && isInvestigation(steps[0])
  return (
    <ol aria-label="Episode timeline" className="flex flex-col gap-2">
      {steps.map((s, i) => (
        <Fragment key={s.idx}>
          {live && i === 0 && <PhaseHeading>Phase 1: investigation</PhaseHeading>}
          {live && i === firstResponse && <PhaseHeading>Phase 2: response</PhaseHeading>}
          <StepCard step={s} />
        </Fragment>
      ))}
    </ol>
  )
}

function PhaseHeading({ children }: { children: React.ReactNode }) {
  return (
    <li className="list-none">
      <h3 className="mt-1 text-base font-semibold">{children}</h3>
    </li>
  )
}
