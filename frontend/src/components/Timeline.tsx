import type { CheckResult, StepOut } from '../api/client'
import { recoveryState } from './recovery'

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

function StepCard({ step }: { step: StepOut }) {
  const p = step.payload
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
 * events and feedback, in trace order. */
export function Timeline({ steps }: { steps: StepOut[] }) {
  return (
    <ol aria-label="Episode timeline" className="flex flex-col gap-2">
      {steps.map((s) => (
        <StepCard key={s.idx} step={s} />
      ))}
    </ol>
  )
}
