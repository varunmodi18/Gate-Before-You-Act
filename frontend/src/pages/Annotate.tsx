import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type ReactNode, type SelectHTMLAttributes } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'

import {
  apiGet,
  apiPost,
  apiPut,
  type AgreementReport,
  type E2Flag,
  type AnnotateCase,
  type AnnotateOverview,
  type Comparison,
  type ValidationReport,
  type Workspace,
  type XCase,
} from '../api/client'
import { JsonField } from '../components/ContextEditor'
import { ErrorBanner } from '../components/ErrorBanner'
import { SqlConsole } from '../components/SqlConsole'
import { VariantGrid } from '../components/VariantGrid'

type Role = 'A' | 'B' | 'adjudicator'
type LabelsDraft = Record<string, unknown>
const ROLES: Role[] = ['A', 'B', 'adjudicator']
const OUTCOMES = [
  'safe_completion',
  'justified_escalation',
  'unnecessary_deferral',
  'refusal',
  'unsafe_execution',
]
const VERDICTS = ['SUPPORTS', 'INSUFFICIENT', 'CONTRADICTED']
const SCOPE = ['host', 'account', 'command', 'time'] as const
const input = 'rounded border border-gray-500 p-1 text-sm'
const ids = (t: string) =>
  t
    .split(/[\s,]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0)
const fmt = (x: number | null | undefined) => (x === null || x === undefined ? '—' : x.toFixed(3))
const isX = (c: AnnotateCase | XCase): c is XCase => (c as XCase).kind === 'end_to_end'
/** Set X is labelled with outcome and justified completion; E and R with outcome and verdict. */
const done = (c: AnnotateCase | XCase, l: LabelsDraft | undefined) =>
  Boolean(l?.correct_outcome) &&
  (isX(c) ? l?.has_justified_completion !== undefined : Boolean(l?.verifier_label))

/** Annotate (plan §E.1 row 4, T4.7): choose a role (local, A-12) and a scenario; agreement panel. */
export function Annotate() {
  const [role, setRole] = useState<Role>('A')
  const list = useQuery({
    queryKey: ['annotate'],
    queryFn: () => apiGet<AnnotateOverview[]>('/annotate'),
  })
  const agreement = useQuery({
    queryKey: ['agreement'],
    queryFn: () => apiGet<AgreementReport>('/annotate/agreement'),
  })
  return (
    <section aria-labelledby="an-title">
      <h1 id="an-title" className="mb-2 text-2xl font-semibold">
        Annotate
      </h1>
      <div className="mb-3 flex flex-col text-sm">
        <label htmlFor="an-role">
          Your role (local selection; the server keeps annotations blind)
        </label>
        <select
          id="an-role"
          className={`${input} max-w-xs`}
          value={role}
          onChange={(e) => setRole(e.target.value as Role)}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r === 'adjudicator' ? 'Adjudicator' : `Annotator ${r}`}
            </option>
          ))}
        </select>
      </div>
      {list.isError && <ErrorBanner error={list.error} />}
      {list.data && list.data.length === 0 && (
        <p className="text-sm">No generated scenarios yet.</p>
      )}
      {list.data && list.data.length > 0 && (
        <table className="mb-4 min-w-full border-collapse text-sm">
          <caption className="sr-only">Scenarios to annotate</caption>
          <thead>
            <tr>
              {['Scenario', 'Split', 'Status', 'Cases', 'Annotator A', 'Annotator B'].map((h) => (
                <th key={h} scope="col" className="border-b border-gray-400 px-2 py-1 text-left">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {list.data.map((s) => (
              <tr key={s.id} className="border-b border-gray-200">
                <th scope="row" className="px-2 py-1 text-left">
                  <Link className="text-blue-800 underline" to={`/annotate/${s.id}?role=${role}`}>
                    {s.id}
                  </Link>
                </th>
                <td className="px-2 py-1">{s.split ?? '—'}</td>
                <td className="px-2 py-1">{s.status}</td>
                <td className="px-2 py-1">
                  {s.cases}
                  {s.kind === 'end_to_end' ? ' (end-to-end window)' : ''}
                </td>
                <td className="px-2 py-1">{s.A}</td>
                <td className="px-2 py-1">{s.B}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {agreement.data && <AgreementPanel report={agreement.data} />}
    </section>
  )
}

function AgreementPanel({ report }: { report: AgreementReport }) {
  const p = report.pooled
  return (
    <section aria-labelledby="ag-title">
      <h2 id="ag-title" className="text-lg font-semibold">
        Agreement before adjudication
      </h2>
      <p className="text-xs text-gray-700">{report.note}</p>
      <table className="min-w-full border-collapse text-sm">
        <caption className="sr-only">Inter-annotator agreement</caption>
        <thead>
          <tr>
            {[
              'Scope',
              'Scenarios',
              'κ outcome (items)',
              'κ actions (items)',
              'Evidence Jaccard (E1; set X: evidence records)',
            ].map((h) => (
              <th key={h} scope="col" className="border-b border-gray-400 px-2 py-1 text-left">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-gray-300 font-semibold">
            <th scope="row" className="px-2 py-1 text-left">
              pooled
            </th>
            <td className="px-2 py-1">{p.scenarios}</td>
            <td className="px-2 py-1">
              {fmt(p.kappa_outcome)} ({p.outcome_items})
            </td>
            <td className="px-2 py-1">
              {fmt(p.kappa_actions)} ({p.action_items})
            </td>
            <td className="px-2 py-1">{fmt(p.jaccard_e1_mean)}</td>
          </tr>
          {report.set_x && (
            <tr className="border-b border-gray-300 font-semibold">
              <th scope="row" className="px-2 py-1 text-left">
                set X (end-to-end windows, separate)
              </th>
              <td className="px-2 py-1">{report.set_x.scenarios}</td>
              <td className="px-2 py-1">
                {fmt(report.set_x.kappa_outcome)} ({report.set_x.outcome_items})
              </td>
              <td className="px-2 py-1">
                {fmt(report.set_x.kappa_actions)} ({report.set_x.action_items})
              </td>
              <td className="px-2 py-1">{fmt(report.set_x.jaccard_x_mean)} (evidence records)</td>
            </tr>
          )}
          {report.per_scenario.map((s) => (
            <tr key={s.scenario_id} className="border-b border-gray-200">
              <th scope="row" className="px-2 py-1 text-left font-normal">
                {s.scenario_id}
              </th>
              <td className="px-2 py-1">1</td>
              <td className="px-2 py-1">
                {fmt(s.kappa_outcome)} ({s.outcome_items})
              </td>
              <td className="px-2 py-1">
                {fmt(s.kappa_actions)} ({s.action_items})
              </td>
              <td className="px-2 py-1">{fmt(s.jaccard_e1_mean)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

/** One scenario for one role. */
export function AnnotateScenario() {
  const { sid = '' } = useParams()
  const [params] = useSearchParams()
  const role = (params.get('role') ?? 'A') as Role
  const ws = useQuery({
    queryKey: ['workspace', sid, role],
    queryFn: () => apiGet<Workspace>(`/annotate/${sid}`, { role }),
  })
  if (ws.isError) return <ErrorBanner error={ws.error} />
  if (!ws.data) return <p>Loading…</p>
  return (
    <section aria-labelledby="as-title">
      <h1 id="as-title" className="mb-1 text-2xl font-semibold">
        Annotate {sid} as {role === 'adjudicator' ? 'Adjudicator' : `annotator ${role}`}
      </h1>
      <p className="mb-3 text-sm">
        Status {ws.data.scenario.status}; target{' '}
        <span className="font-mono">{ws.data.scenario.target_host}</span>;{' '}
        {ws.data.both_submitted
          ? 'both annotators have submitted'
          : ws.data.other_submitted
            ? 'the other annotator has submitted'
            : 'the other annotator has not submitted'}
      </p>
      {role === 'adjudicator' ? (
        <Adjudicator sid={sid} ws={ws.data} />
      ) : (
        <Annotator key={ws.data.own?.submitted_at ?? 'draft'} sid={sid} role={role} ws={ws.data} />
      )}
    </section>
  )
}

function Annotator({ sid, role, ws }: { sid: string; role: 'A' | 'B'; ws: Workspace }) {
  const qc = useQueryClient()
  const [labels, setLabels] = useState<Record<string, LabelsDraft>>(() => ws.own?.labels ?? {})
  const [notes, setNotes] = useState<Record<string, unknown>>(() => ws.own?.notes ?? {})
  const [variant, setVariant] = useState(ws.cases[0]?.variant ?? 'E1')
  const locked = Boolean(ws.own?.submitted_at)
  const save = useMutation({
    mutationFn: () => apiPut(`/annotate/${sid}/${role}`, { labels, notes }),
  })
  const submit = useMutation({
    mutationFn: async () => {
      await apiPut(`/annotate/${sid}/${role}`, { labels, notes })
      return apiPost(`/annotate/${sid}/${role}/submit`, {})
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['workspace', sid, role] }),
  })
  const c = ws.cases.find((x) => x.variant === variant)
  return (
    <>
      <div role="group" aria-label="Cases" className="mb-3 flex flex-wrap gap-2">
        {ws.cases.map((x) => (
          <button
            key={x.variant}
            type="button"
            aria-pressed={x.variant === variant}
            className="rounded border border-gray-600 px-2 py-1 text-sm"
            onClick={() => setVariant(x.variant)}
          >
            {x.variant}
            {done(x, labels[x.variant]) ? ' ✓' : ''}
          </button>
        ))}
      </div>
      {locked && <p className="mb-2 text-sm font-semibold">Submitted and locked.</p>}
      {c && isX(c) && (
        <XCaseForm
          key={c.variant}
          sid={sid}
          c={c}
          locked={locked}
          value={labels[c.variant] ?? {}}
          onChange={(v) => setLabels((l) => ({ ...l, [c.variant]: v }))}
        />
      )}
      {c && !isX(c) && (
        <CaseForm
          key={c.variant}
          sid={sid}
          c={c}
          locked={locked}
          value={labels[c.variant] ?? {}}
          onChange={(v) => setLabels((l) => ({ ...l, [c.variant]: v }))}
          checklist={
            (
              notes.checklist as Record<string, Record<string, Record<string, boolean>>> | undefined
            )?.[c.variant] ?? {}
          }
          onChecklist={(cl) =>
            setNotes((n) => ({
              ...n,
              checklist: { ...((n.checklist as object) ?? {}), [c.variant]: cl },
            }))
          }
        />
      )}
      {!locked && (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            className="rounded border border-gray-600 px-3 py-1"
            onClick={() => save.mutate()}
          >
            Save draft
          </button>
          <button
            type="button"
            className="rounded bg-blue-800 px-3 py-1 text-white"
            onClick={() => submit.mutate()}
          >
            Submit (locks)
          </button>
        </div>
      )}
      <div aria-live="polite">
        {save.isSuccess && <p className="text-sm">✓ Draft saved.</p>}
        {save.isError && <ErrorBanner error={save.error} />}
        {submit.isError && <ErrorBanner error={submit.error} />}
      </div>
    </>
  )
}

function CaseForm({
  sid,
  c,
  value,
  onChange,
  locked,
  checklist,
  onChecklist,
}: {
  sid: string
  c: AnnotateCase
  value: LabelsDraft
  onChange: (v: LabelsDraft) => void
  locked: boolean
  checklist: Record<string, Record<string, boolean>>
  onChecklist: (cl: Record<string, Record<string, boolean>>) => void
}) {
  const set = (patch: LabelsDraft) => onChange({ ...value, ...patch })
  const [picked, setPicked] = useState<number | null>(null)
  const list = (k: string) => ((value[k] as number[] | undefined) ?? []).join(', ')
  const user = c.prompt.messages.find((m) => m.role === 'user')?.content ?? ''
  const system = c.prompt.messages.find((m) => m.role === 'system')?.content ?? ''
  return (
    <fieldset disabled={locked} className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <legend className="sr-only">Case {c.variant}</legend>
      <section aria-labelledby={`vv-${c.variant}`}>
        <h2 id={`vv-${c.variant}`} className="font-semibold">
          Verifier view (exactly as C4 receives it)
        </h2>
        {!c.prompt.reference_available && (
          <p className="text-sm text-red-900">
            ✕ No retrieval index here: the REFERENCE block is missing (make index).
          </p>
        )}
        <details className="text-sm">
          <summary className="cursor-pointer">System prompt</summary>
          <pre
            tabIndex={0}
            aria-label="System prompt"
            className="max-h-64 overflow-auto whitespace-pre-wrap bg-gray-100 p-2 text-xs"
          >
            {system}
          </pre>
        </details>
        <pre
          tabIndex={0}
          aria-label={`Verifier prompt for ${c.variant}`}
          className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-all bg-gray-100 p-2 text-xs"
        >
          {user}
        </pre>
        <p className="mt-1 text-sm">
          Policy for the package call: <strong>{c.policy.decision}</strong> ({c.policy.rule_id});
          approval script <span className="font-mono">{c.approval_script}</span>
          {c.e4_kind ? `; E4 kind ${c.e4_kind}` : ''}
        </p>
        <TicketChecklist c={c} value={checklist} onChange={onChecklist} />
        {c.e2 && <E2Sample sid={sid} c={c} />}
        {(c.marked_labels ?? []).length > 0 && (
          <div className="mt-2 rounded border border-amber-700 bg-amber-50 p-2 text-sm text-amber-950">
            <h3 className="font-semibold">Marked labels in the case file</h3>
            <ul className="list-disc pl-5">
              {(c.marked_labels ?? []).map((m, i) => (
                <li key={i}>
                  <span className="font-mono">
                    {m.list}: {m.tool} {JSON.stringify(m.args)}
                  </span>
                  {m.list === 'permitted' &&
                    ` (requires approval: ${m.requires_approval ? 'yes' : 'no'}; fulfils: ${m.fulfils ? 'yes' : 'no'})`}{' '}
                  — <strong>{m.note}</strong>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
      <section aria-labelledby={`lf-${c.variant}`} className="flex flex-col gap-2 text-sm">
        <h2 id={`lf-${c.variant}`} className="font-semibold">
          Labels for {c.id}
        </h2>
        {(
          [
            ['Verifier label', 'verifier_label', VERDICTS],
            ['Correct outcome', 'correct_outcome', OUTCOMES],
            ['Initial gate label', 'initial_gate_label', ['admit', 'reject']],
          ] as const
        ).map(([label, key, opts]) => (
          <LabeledSelect
            key={key}
            label={label}
            value={(value[key] as string) ?? ''}
            onChange={(e) => set({ [key]: e.target.value || null })}
          >
            <option value="">—</option>
            {opts.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </LabeledSelect>
        ))}
        <label className="flex flex-col">
          Gold technique (scoring only)
          <input
            className={input}
            value={(value.technique_gold as string) ?? ''}
            onChange={(e) => set({ technique_gold: e.target.value || null })}
          />
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={Boolean(value.has_justified_completion)}
            onChange={(e) => set({ has_justified_completion: e.target.checked })}
          />
          Has a justified completion
        </label>
        <label className="flex flex-col">
          Evidence (counterfactual, E1 version)
          <input
            className={`${input} font-mono`}
            value={list('evidence_counterfactual')}
            onChange={(e) => set({ evidence_counterfactual: ids(e.target.value) })}
          />
        </label>
        <label className="flex flex-col">
          Evidence (retrievable in this case)
          <input
            className={`${input} font-mono`}
            value={list('evidence_retrievable')}
            onChange={(e) => set({ evidence_retrievable: ids(e.target.value) })}
          />
        </label>
        <JsonField
          label="Permitted (JSON list: tool, args, requires_approval, fulfils)"
          value={value.permitted ?? []}
          onChange={(v) => set({ permitted: v })}
        />
        <JsonField
          label="Prohibited (JSON list; * wildcards allowed)"
          value={value.prohibited ?? []}
          onChange={(v) => set({ prohibited: v })}
        />
        <JsonField
          label="Acceptable escalations (JSON list of predicates)"
          value={value.acceptable_escalations ?? []}
          onChange={(v) => set({ acceptable_escalations: v })}
        />
        <DecisiveEditor
          sid={sid}
          c={c}
          value={(value.decisive as Record<string, unknown>[] | undefined) ?? []}
          onChange={(d) => set({ decisive: d })}
        />
        <SqlConsole
          title="Evidence picker (this case's database)"
          queryPath={`/annotate/${sid}/cases/${c.variant}/query`}
          onOpenRecord={setPicked}
        />
        {picked !== null && (
          <div role="group" aria-label={`Record ${picked}`} className="flex gap-2">
            <span>Record {picked}:</span>
            {(['evidence_counterfactual', 'evidence_retrievable'] as const).map((k) => (
              <button
                key={k}
                type="button"
                className="underline"
                onClick={() =>
                  set({ [k]: [...new Set([...((value[k] as number[]) ?? []), picked])] })
                }
              >
                Add to {k.replace('evidence_', '')}
              </button>
            ))}
          </div>
        )}
      </section>
    </fieldset>
  )
}

function TicketChecklist({
  c,
  value,
  onChange,
}: {
  c: AnnotateCase
  value: Record<string, Record<string, boolean>>
  onChange: (v: Record<string, Record<string, boolean>>) => void
}) {
  const ticket = c.tickets.find((t) => t.approved)
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="min-w-full border-collapse text-xs">
        <caption className="text-left text-sm font-semibold">
          Ticket-scope checklist (
          {ticket
            ? `${ticket.id}: ${ticket.host}, ${ticket.account}, ${ticket.command_pattern}, ${ticket.start}–${ticket.end}`
            : 'no approved ticket'}
          )
        </caption>
        <thead>
          <tr>
            {[
              'Event',
              'Host',
              'Account',
              'Command',
              'Time (UTC)',
              ...SCOPE.map((s) => `in scope: ${s}`),
            ].map((h) => (
              <th key={h} scope="col" className="border-b border-gray-400 px-1 text-left">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {c.suspicious.map((e) => (
            <tr key={e.record_id} className="border-b border-gray-200">
              <th scope="row" className="px-1 text-left">
                {e.record_id}
              </th>
              <td className="px-1">{e.host}</td>
              <td className="px-1">{e.account}</td>
              <td className="px-1 font-mono">{(e.command ?? '').slice(0, 60)}</td>
              <td className="px-1">{e.ts}</td>
              {SCOPE.map((s) => (
                <td key={s} className="px-1">
                  <input
                    type="checkbox"
                    aria-label={`Event ${e.record_id} ${s} in scope`}
                    checked={Boolean(value[e.record_id]?.[s])}
                    onChange={(ev) =>
                      onChange({
                        ...value,
                        [e.record_id]: { ...value[e.record_id], [s]: ev.target.checked },
                      })
                    }
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DecisiveEditor({
  sid,
  c,
  value,
  onChange,
}: {
  sid: string
  c: AnnotateCase
  value: Record<string, unknown>[]
  onChange: (v: Record<string, unknown>[]) => void
}) {
  const [kind, setKind] = useState<'field' | 'ticket' | 'absent'>('field')
  const [record, setRecord] = useState(String(c.package.cited[0] ?? ''))
  const fields = Object.keys(c.structured[record] ?? {})
  const [field, setField] = useState('')
  const [contains, setContains] = useState('')
  const [ticketId, setTicketId] = useState(c.tickets[0]?.id ?? '')
  const [scope, setScope] = useState<string[]>(['host', 'time'])
  const [absent, setAbsent] = useState('')
  const check = useMutation({
    mutationFn: () =>
      apiPost<{ ok: boolean; problems: string[] }>(
        `/annotate/${sid}/cases/${c.variant}/decisive-check`,
        { entries: value },
      ),
  })
  const entry = (): Record<string, unknown> | null => {
    if (kind === 'field')
      return record && (field || fields[0]) && contains
        ? { record_id: Number(record), field: field || fields[0], contains }
        : null
    if (kind === 'ticket') return ticketId && scope.length ? { ticket_id: ticketId, scope } : null
    return Number(absent) > 0 ? { absent_record_id: Number(absent) } : null
  }
  return (
    <fieldset className="rounded border border-gray-400 p-2">
      <legend className="font-semibold">Decisive facts (must hold in the rendered prompt)</legend>
      <ul className="mb-1 list-disc pl-5">
        {value.map((d, i) => (
          <li key={i}>
            <code>{JSON.stringify(d)}</code>{' '}
            <button
              type="button"
              className="underline"
              onClick={() => onChange(value.filter((_, j) => j !== i))}
            >
              remove entry {i + 1}
            </button>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-end gap-2">
        <LabeledSelect
          label="Kind"
          value={kind}
          onChange={(e) => setKind(e.target.value as typeof kind)}
        >
          <option value="field">record field contains</option>
          <option value="ticket">ticket scope</option>
          <option value="absent">record absent</option>
        </LabeledSelect>
        {kind === 'field' && (
          <>
            <LabeledSelect
              label="Cited record"
              value={record}
              onChange={(e) => setRecord(e.target.value)}
            >
              {c.package.cited.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </LabeledSelect>
            <LabeledSelect
              label="Field"
              value={field || fields[0] || ''}
              onChange={(e) => setField(e.target.value)}
            >
              {fields.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </LabeledSelect>
            <label className="flex flex-col">
              Contains
              <input
                className={input}
                value={contains}
                onChange={(e) => setContains(e.target.value)}
              />
            </label>
          </>
        )}
        {kind === 'ticket' && (
          <>
            <LabeledSelect
              label="Ticket"
              value={ticketId}
              onChange={(e) => setTicketId(e.target.value)}
            >
              {c.tickets.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.id}
                </option>
              ))}
            </LabeledSelect>
            {SCOPE.map((s) => (
              <label key={s} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={scope.includes(s)}
                  onChange={(e) =>
                    setScope(e.target.checked ? [...scope, s] : scope.filter((x) => x !== s))
                  }
                />
                {s}
              </label>
            ))}
          </>
        )}
        {kind === 'absent' && (
          <label className="flex flex-col">
            Record id
            <input className={input} value={absent} onChange={(e) => setAbsent(e.target.value)} />
          </label>
        )}
        <button
          type="button"
          className="rounded border border-gray-600 px-2"
          onClick={() => {
            const e = entry()
            if (e) onChange([...value, e])
          }}
        >
          Add entry
        </button>
        <button
          type="button"
          className="rounded border border-gray-600 px-2"
          onClick={() => check.mutate()}
        >
          Check entries
        </button>
      </div>
      <div aria-live="polite" className="mt-1">
        {check.data &&
          (check.data.ok ? (
            <p>✓ Every entry holds in the prompt.</p>
          ) : (
            <ul className="list-disc pl-5 text-red-900">
              {check.data.problems.map((p) => (
                <li key={p}>✕ {p}</li>
              ))}
            </ul>
          ))}
        {check.isError && <ErrorBanner error={check.error} />}
      </div>
    </fieldset>
  )
}

function Adjudicator({ sid, ws }: { sid: string; ws: Workspace }) {
  const cmp = useQuery({
    queryKey: ['compare', sid],
    queryFn: () => apiGet<Comparison>(`/annotate/${sid}/compare`),
    enabled: ws.both_submitted,
  })
  if (!ws.both_submitted)
    return (
      <p className="text-sm">The annotations stay blind until both annotators have submitted.</p>
    )
  if (cmp.isError) return <ErrorBanner error={cmp.error} />
  if (!cmp.data) return <p>Loading…</p>
  return (
    <AdjudicationForm
      key={sid}
      sid={sid}
      cmp={cmp.data}
      variants={ws.cases.map((c) => c.variant)}
    />
  )
}

function AdjudicationForm({
  sid,
  cmp,
  variants,
}: {
  sid: string
  cmp: Comparison
  variants: string[]
}) {
  const qc = useQueryClient()
  const [final, setFinal] = useState<Record<string, LabelsDraft>>(() =>
    Object.fromEntries(variants.map((v) => [v, cmp.A.labels[v] ?? {}])),
  )
  const [version, setVersion] = useState(0)
  const run = useMutation({
    mutationFn: () =>
      apiPost<{ validation: ValidationReport }>(`/annotate/${sid}/adjudicate`, { labels: final }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['workspace', sid] }),
  })
  return (
    <section aria-labelledby="adj-title">
      <h2 id="adj-title" className="text-lg font-semibold">
        Adjudication
      </h2>
      <table className="mb-3 min-w-full border-collapse text-sm">
        <caption className="sr-only">Disagreements per case</caption>
        <thead>
          <tr>
            {['Case', 'Fields A and B disagree on', 'Start from'].map((h) => (
              <th key={h} scope="col" className="border-b border-gray-400 px-2 py-1 text-left">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {variants.map((v) => (
            <tr key={v} className="border-b border-gray-200">
              <th scope="row" className="px-2 py-1 text-left font-mono">
                {v}
              </th>
              <td className="px-2 py-1">{(cmp.disagreements[v] ?? []).join(', ') || 'agree'}</td>
              <td className="px-2 py-1">
                {(['A', 'B'] as const).map((r) => (
                  <button
                    key={r}
                    type="button"
                    className="mr-2 underline"
                    onClick={() => {
                      setFinal((f) => ({ ...f, [v]: cmp[r].labels[v] ?? {} }))
                      setVersion((x) => x + 1)
                    }}
                  >
                    {v}: use {r}
                  </button>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {variants.map((v) => (
        <JsonField
          key={`${v}-${version}`}
          label={`Final labels for ${v}`}
          rows={6}
          value={final[v]}
          onChange={(x) => setFinal((f) => ({ ...f, [v]: x as LabelsDraft }))}
        />
      ))}
      <button
        type="button"
        className="mt-3 rounded bg-blue-800 px-3 py-1 text-white"
        onClick={() => run.mutate()}
      >
        Adjudicate and write labels
      </button>
      <div aria-live="polite">
        {run.isError && <ErrorBanner error={run.error} />}
        {run.data &&
          (variants.length === 1 && variants[0] === 'X' ? (
            <>
              <p className="text-sm">✓ Final labels written to e2e.json.</p>
              <ul aria-label="Validator results for the window" className="text-sm">
                {run.data.validation.checks.map((ch) => (
                  <li key={ch.check}>
                    <span className="font-mono">{ch.check}</span>: {ch.status} — {ch.message}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <>
              <p className="text-sm">✓ Final labels written to the case files.</p>
              <VariantGrid report={run.data.validation} />
            </>
          ))}
      </div>
    </section>
  )
}

/** A select with an explicit label (its accessible name is the label alone, not the options). */
function LabeledSelect({
  label,
  children,
  ...props
}: { label: ReactNode; children: ReactNode } & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId()
  return (
    <div className="flex flex-col">
      <label htmlFor={id}>{label}</label>
      <select id={id} className={input} {...props}>
        {children}
      </select>
    </div>
  )
}

/** E2's sampled records with a per-record "attack-related" flag (team decision after M4): a
 * flagged record is replaced by the next seeded candidate, and the replacement is logged. */
function E2Sample({ sid, c }: { sid: string; c: AnnotateCase }) {
  const qc = useQueryClient()
  const [params] = useSearchParams()
  const role = params.get('role') ?? 'A'
  const [reason, setReason] = useState('')
  const flag = useMutation({
    mutationFn: (recordId: number) =>
      apiPost<E2Flag>(`/annotate/${sid}/e2-flag`, { record_id: recordId, role, reason }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['workspace', sid] }),
  })
  const e2 = c.e2
  if (!e2) return null
  return (
    <section aria-labelledby={`e2-${c.id}`} className="mt-3 text-sm">
      <h3 id={`e2-${c.id}`} className="font-semibold">
        E2 sampled records
      </h3>
      <p className="text-xs text-gray-700">
        Sampled as unrelated evidence. If one is part of the attack, flag it: E2 is regenerated with
        the next seeded record, and the replacement is logged for both annotators.
      </p>
      <label className="flex flex-col">
        Reason for a flag (optional)
        <input className={input} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <table className="mt-1 min-w-full border-collapse text-xs">
        <caption className="sr-only">E2 sampled records</caption>
        <thead>
          <tr>
            {['Record', 'Table', 'Host', 'PID', 'What it shows', ''].map((h, i) => (
              <th key={i} scope="col" className="border-b border-gray-400 px-1 text-left">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {e2.sampled.map((r) => (
            <tr key={r.record_id} className="border-b border-gray-200">
              <th scope="row" className="px-1 text-left">
                {r.record_id}
              </th>
              <td className="px-1">{r.table}</td>
              <td className="px-1">{r.host}</td>
              <td className="px-1">{r.pid ?? '—'}</td>
              <td className="px-1 font-mono">{r.summary}</td>
              <td className="px-1">
                <button
                  type="button"
                  className="underline"
                  disabled={flag.isPending}
                  onClick={() => flag.mutate(r.record_id)}
                >
                  Flag record {r.record_id} as attack-related
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div aria-live="polite">
        {flag.isError && <ErrorBanner error={flag.error} />}
        {flag.data && (
          <p>
            ✓ Record {flag.data.record_id} replaced by{' '}
            {flag.data.replaced_by.join(', ') || 'nothing'}.
          </p>
        )}
      </div>
      {e2.flags.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs">
          {e2.flags.map((f, i) => (
            <li key={i}>
              {f.at.slice(0, 19)}: annotator {f.flagged_by} flagged {f.record_id}
              {f.reason ? ` (${f.reason})` : ''} → replaced by {f.replaced_by.join(', ') || '—'}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** The one case of an end-to-end window (set X, decision D): request, window and its technique
 * metadata (Q-X3), scope, trusted context with the second member's check (Q-X4), and only the
 * set X label fields; evidence is picked from the unmodified window database. */
function XCaseForm({
  sid,
  c,
  value,
  onChange,
  locked,
}: {
  sid: string
  c: XCase
  value: LabelsDraft
  onChange: (v: LabelsDraft) => void
  locked: boolean
}) {
  const set = (patch: LabelsDraft) => onChange({ ...value, ...patch })
  const [picked, setPicked] = useState<number | null>(null)
  const hjc = value.has_justified_completion
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <section aria-labelledby="x-ctx" className="flex flex-col gap-2 text-sm">
        <h2 id="x-ctx" className="font-semibold">
          End-to-end window {c.window.id}
        </h2>
        <p>
          {c.window.title}; techniques in the window&apos;s metadata:{' '}
          <span className="font-mono">{c.window.techniques.join(', ') || 'none'}</span>
        </p>
        <p>
          Request: <q>{c.request.text}</q> ({c.request.objective})
        </p>
        <p>
          Policy for the requested action: <strong>{c.policy.decision}</strong> ({c.policy.rule_id}
          ); approval script <span className="font-mono">{c.approval_script}</span>
        </p>
        <div>
          <h3 className="font-semibold">Scope (rule A)</h3>
          <p>
            Hosts <span className="font-mono">{c.scope.hosts.join(', ')}</span>; accounts{' '}
            <span className="font-mono">{c.scope.accounts.join(', ') || 'none'}</span>; IPs{' '}
            <span className="font-mono">{c.scope.ips.join(', ') || 'none'}</span>
          </p>
          <p className="font-mono text-xs break-all">
            Processes:{' '}
            {c.scope.processes.map((p) => `${p.pid} ${p.image ?? '?'}`).join('; ') || 'none'}
          </p>
        </div>
        <details>
          <summary className="cursor-pointer">Trusted context</summary>
          <pre
            tabIndex={0}
            aria-label="Trusted context"
            className="max-h-64 overflow-auto whitespace-pre-wrap bg-gray-100 p-2 text-xs"
          >
            {JSON.stringify(c.trusted_context, null, 2)}
          </pre>
        </details>
        <ContextCheck sid={sid} c={c} />
        <p className="text-xs text-gray-700">
          Not labelled for set X: {c.not_labelled.join(', ')} (technique ground truth is the
          metadata plus adjudicated incidental techniques).
        </p>
      </section>
      <fieldset disabled={locked} className="flex flex-col gap-2 text-sm">
        <legend className="font-semibold">Labels for {c.id}</legend>
        <LabeledSelect
          label="Correct outcome"
          value={(value.correct_outcome as string) ?? ''}
          onChange={(e) => set({ correct_outcome: e.target.value || null })}
        >
          <option value="">—</option>
          {OUTCOMES.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </LabeledSelect>
        <LabeledSelect
          label="Has a justified completion"
          value={hjc === undefined || hjc === null ? '' : hjc ? 'yes' : 'no'}
          onChange={(e) =>
            set({
              has_justified_completion: e.target.value === '' ? null : e.target.value === 'yes',
            })
          }
        >
          <option value="">—</option>
          <option value="yes">yes</option>
          <option value="no">no</option>
        </LabeledSelect>
        <label className="flex flex-col">
          Evidence records (justify the action)
          <input
            className={`${input} font-mono`}
            value={((value.evidence_retrievable as number[] | undefined) ?? []).join(', ')}
            onChange={(e) => set({ evidence_retrievable: ids(e.target.value) })}
          />
        </label>
        <JsonField
          label="Permitted (JSON list: tool, args, requires_approval, fulfils)"
          value={value.permitted ?? []}
          onChange={(v) => set({ permitted: v })}
        />
        <JsonField
          label="Prohibited (JSON list; * wildcards allowed)"
          value={value.prohibited ?? []}
          onChange={(v) => set({ prohibited: v })}
        />
        <JsonField
          label="Acceptable escalations (JSON list of predicates)"
          value={value.acceptable_escalations ?? []}
          onChange={(v) => set({ acceptable_escalations: v })}
        />
        <SqlConsole
          title="Evidence picker (the window's database)"
          queryPath={`/annotate/${sid}/cases/X/query`}
          onOpenRecord={setPicked}
        />
        {picked !== null && (
          <button
            type="button"
            className="self-start underline"
            onClick={() =>
              set({
                evidence_retrievable: [
                  ...new Set([...((value.evidence_retrievable as number[]) ?? []), picked]),
                ],
              })
            }
          >
            Add record {picked} to the evidence records
          </button>
        )}
      </fieldset>
    </div>
  )
}

/** Q-X4: the trusted context is authored once and checked by a second member. */
function ContextCheck({ sid, c }: { sid: string; c: XCase }) {
  const qc = useQueryClient()
  const [by, setBy] = useState('')
  const check = useMutation({
    mutationFn: () => apiPost(`/annotate/${sid}/context-check`, { by }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['workspace', sid] }),
  })
  const id = useId()
  return (
    <div className="rounded border border-gray-400 p-2">
      <p>
        Context author: {c.context_author ?? 'not recorded'}; checked by:{' '}
        {c.context_checked_by ?? 'nobody yet'}
      </p>
      {!c.context_checked_by && (
        <div className="mt-1 flex items-end gap-2">
          <label htmlFor={id} className="flex flex-col">
            Your name (a member other than the author)
            <input id={id} className={input} value={by} onChange={(e) => setBy(e.target.value)} />
          </label>
          <button
            type="button"
            className="rounded border border-gray-600 px-2 py-1"
            disabled={!by.trim() || check.isPending}
            onClick={() => check.mutate()}
          >
            I checked the context
          </button>
        </div>
      )}
      {check.isError && <ErrorBanner error={check.error} />}
    </div>
  )
}
