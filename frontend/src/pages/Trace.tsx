import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { Link, useParams } from 'react-router'

import { apiGet, apiPut, type EpisodeDetail, type EpisodeSummary } from '../api/client'
import { ErrorBanner } from '../components/ErrorBanner'
import { RecoveryMeter, Timeline } from '../components/Timeline'
import { InvestigationPanel, OutcomePanel } from './Console'

const th = 'border-b border-gray-400 px-2 py-1 text-left'

/** Traces (plan §E.1 row 10): every episode, newest first. */
export function Traces() {
  const eps = useQuery({
    queryKey: ['episodes'],
    queryFn: () => apiGet<EpisodeSummary[]>('/episodes', { limit: 200 }),
  })
  return (
    <section aria-labelledby="traces-title">
      <h1 id="traces-title" className="mb-2 text-2xl font-semibold">
        Traces
      </h1>
      {eps.isError && <ErrorBanner error={eps.error} />}
      {eps.data && eps.data.length === 0 && <p className="text-sm">No episodes yet.</p>}
      {eps.data && eps.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse text-sm">
            <caption className="sr-only">Episodes</caption>
            <thead>
              <tr>
                {[
                  'Episode',
                  'Run',
                  'Case',
                  'System',
                  'Run idx',
                  'Status',
                  'Ended',
                  'Outcome',
                  'Tags',
                ].map((h) =>
                  // prettier-ignore
                  <th key={h} scope="col" className={th}>
                    {h}
                  </th>,
                )}
              </tr>
            </thead>
            <tbody>
              {eps.data.map((e) => (
                <tr key={e.id} className="border-b border-gray-200">
                  <th scope="row" className="px-2 py-1 text-left">
                    <Link className="text-blue-800 underline" to={`/traces/${e.id}`}>
                      {e.starred ? '★ ' : ''}Episode {e.id}
                    </Link>
                  </th>
                  <td className="px-2 py-1">{e.run_id ?? 'console'}</td>
                  <td className="px-2 py-1 font-mono">{e.case_id}</td>
                  <td className="px-2 py-1 font-mono">{e.system}</td>
                  <td className="px-2 py-1">{e.run_idx}</td>
                  <td className="px-2 py-1">{e.status}</td>
                  <td className="px-2 py-1">{e.terminal_state ?? '—'}</td>
                  <td className="px-2 py-1">{e.outcome ?? 'unscored'}</td>
                  <td className="px-2 py-1">{(e.tags ?? []).join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

/** One episode: full step list, calls and escalations against the case labels, error tags. */
export function Trace() {
  const { episodeId = '' } = useParams()
  const qc = useQueryClient()
  const ep = useQuery({
    queryKey: ['episode', episodeId],
    queryFn: () => apiGet<EpisodeDetail>(`/episodes/${episodeId}`),
  })
  // The notice lives here: saving refetches the episode and remounts the editor.
  const save = useMutation({
    mutationFn: (body: TagBody) => apiPut(`/episodes/${episodeId}/tags`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['episode', episodeId] }),
  })
  if (ep.isError) return <ErrorBanner error={ep.error} />
  if (!ep.data) return <p>Loading…</p>
  const e = ep.data
  return (
    <section aria-labelledby="trace-title">
      <h1 id="trace-title" className="mb-2 text-2xl font-semibold">
        Episode {e.id}
      </h1>
      <p className="mb-2 text-sm">
        Case <span className="font-mono">{e.case_id}</span> · {e.system} · run {e.run_idx}{' '}
        (temperature {e.temperature}, seed {e.seed}) · {e.run_id ? `run ${e.run_id}` : 'console'}
        {e.interactive_approval && ' · demo-interactive approval (not used in experiments)'}
      </p>
      {e.request && (
        <p className="mb-2 text-sm">
          Request: <q>{e.request.text}</q>
        </p>
      )}
      {e.investigation && <InvestigationPanel inv={e.investigation} />}
      {e.status !== 'running' && <OutcomePanel ep={e} />}
      <TagEditor
        key={JSON.stringify([e.tags, e.starred, e.note])}
        ep={e}
        onSave={(body) => save.mutate(body)}
      />
      <span aria-live="polite" className="text-sm">
        {save.isSuccess && 'Saved.'}
      </span>
      {save.isError && <ErrorBanner error={save.error} />}
      <h2 className="mt-4 mb-1 text-lg font-semibold">Calls against the labels</h2>
      <CallsVsLabels ep={e} />
      <h2 className="mt-4 mb-1 text-lg font-semibold">Steps</h2>
      <div className="mb-2">
        <RecoveryMeter steps={e.steps} />
      </div>
      <Timeline steps={e.steps} />
    </section>
  )
}

function CallsVsLabels({ ep }: { ep: EpisodeDetail }) {
  const labels = (ep.labels ?? {}) as Record<string, unknown>
  const scored = ep.scoring !== null
  return (
    <>
      {!scored && (
        <p className="text-sm">Not scored: the case&apos;s labels are pending, so no classes.</p>
      )}
      <table className="mb-2 min-w-full border-collapse text-sm">
        <caption className="sr-only">Tool calls with their classes</caption>
        <thead>
          <tr>
            {['Tool', 'Arguments', 'Cited', 'Admitted', 'Call class', 'Escalation class'].map(
              (h) => (
                <th key={h} scope="col" className={th}>
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {ep.tool_calls.map((c, i) => (
            <tr key={i} className="border-b border-gray-200">
              <th scope="row" className="px-2 py-1 text-left font-mono font-normal">
                {c.tool}
              </th>
              <td className="px-2 py-1 font-mono text-xs break-all">{JSON.stringify(c.args)}</td>
              <td className="px-2 py-1 font-mono">{c.cited.join(', ')}</td>
              <td className="px-2 py-1">{c.admitted ? 'yes' : 'no'}</td>
              <td className="px-2 py-1">{c.call_class ?? '—'}</td>
              <td className="px-2 py-1">{c.escalation_class ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {scored && (
        <details className="text-sm">
          <summary>Case labels (permitted, prohibited, acceptable escalations)</summary>
          <pre
            tabIndex={0}
            aria-label="Case labels"
            className="overflow-auto bg-gray-50 p-1 text-xs"
          >
            {JSON.stringify(
              {
                correct_outcome: labels.correct_outcome,
                permitted: labels.permitted,
                prohibited: labels.prohibited,
                acceptable_escalations: labels.acceptable_escalations,
              },
              null,
              1,
            )}
          </pre>
        </details>
      )}
    </>
  )
}

interface TagBody {
  tags: string[]
  starred: boolean
  note: string | null
}

function TagEditor({ ep, onSave }: { ep: EpisodeDetail; onSave: (body: TagBody) => void }) {
  const [tags, setTags] = useState<string[]>(ep.tags)
  const [starred, setStarred] = useState(ep.starred)
  const [note, setNote] = useState(ep.note ?? '')
  const noteId = useId()
  return (
    <form
      className="mt-3 rounded border border-gray-400 p-2"
      onSubmit={(e) => {
        e.preventDefault()
        onSave({ tags, starred, note: note || null })
      }}
    >
      <fieldset>
        <legend className="text-sm font-semibold">Error tags (taxonomy)</legend>
        <div className="flex flex-wrap gap-3">
          {ep.error_tags.map((t) => (
            <label key={t} className="flex items-center gap-1 text-sm">
              <input
                type="checkbox"
                checked={tags.includes(t)}
                onChange={() =>
                  setTags((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]))
                }
              />
              {t.replaceAll('_', ' ')}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="mt-2 flex items-center gap-1 text-sm">
        <input type="checkbox" checked={starred} onChange={(e) => setStarred(e.target.checked)} />
        One of the annotated traces (star)
      </label>
      <div className="mt-2 flex flex-col text-sm">
        <label htmlFor={noteId}>Note</label>
        <textarea
          id={noteId}
          className="rounded border border-gray-500 p-1"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
      <button type="submit" className="mt-2 rounded bg-blue-800 px-3 py-1 text-white">
        Save tags
      </button>
    </form>
  )
}
