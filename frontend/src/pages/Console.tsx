import { useMutation, useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useId, useState } from 'react'
import { Link } from 'react-router'

import {
  API_BASE,
  apiGet,
  apiPost,
  type ConsoleCase,
  type ConsoleSystem,
  type EpisodeDetail,
  type EpisodeSummary,
  type Investigation,
  type StepOut,
} from '../api/client'
import { ErrorBanner } from '../components/ErrorBanner'
import { isInvestigation } from '../components/recovery'
import { RecoveryMeter, Timeline } from '../components/Timeline'

type Schedule = Record<string, { temperature: number; seed: number }>

/** Steps of one episode: the SSE stream ``/episodes/{id}/stream`` where the browser has
 * EventSource, otherwise polling. ``done`` carries the final summary; ``onEnd`` is called once. */
function useEpisode(id: number, onEnd: (id: number) => void) {
  const [steps, setSteps] = useState<StepOut[]>([])
  const [pending, setPending] = useState<{ call: string } | null>(null)
  const [done, setDone] = useState<EpisodeSummary | null>(null)
  useEffect(() => {
    if (typeof EventSource !== 'undefined') {
      const es = new EventSource(`${API_BASE}/episodes/${id}/stream`)
      const add = (e: MessageEvent<string>) => {
        const s = JSON.parse(e.data) as StepOut
        setSteps((cur) => (cur.some((x) => x.idx === s.idx) ? cur : [...cur, s]))
        if (s.kind === 'approval') setPending(null)
      }
      es.addEventListener('step', add as EventListener)
      es.addEventListener('gate', add as EventListener)
      es.addEventListener('approval_request', ((e: MessageEvent<string>) =>
        setPending(JSON.parse(e.data) as { call: string })) as EventListener)
      const end = (e: MessageEvent<string>) => {
        setDone(JSON.parse(e.data) as EpisodeSummary)
        setPending(null)
        es.close()
        onEnd(id)
      }
      es.addEventListener('outcome', end as EventListener)
      es.addEventListener('error', ((e: MessageEvent<string>) => {
        if (e.data) end(e)
        else es.close()
      }) as EventListener)
      return () => es.close()
    }
    let stop = false
    const poll = async () => {
      while (!stop) {
        const ep = await apiGet<EpisodeDetail>(`/episodes/${id}`)
        setSteps(ep.steps)
        setPending(ep.pending_approval)
        if (ep.status !== 'running') {
          setDone(ep)
          onEnd(id)
          return
        }
        await new Promise((r) => setTimeout(r, 300))
      }
    }
    void poll()
    return () => {
      stop = true
    }
  }, [id, onEnd])
  return { steps, pending, done }
}

/** Agent Console (plan §E.1 row 6): one live episode with the same gate, tools and loop as Exp 2,
 * or, on a window (an end-to-end case, T6.3), the Exp 3 episode: a live investigation of the
 * window and then the response from its handover. */
export function Console() {
  const cases = useQuery({
    queryKey: ['console-cases'],
    queryFn: () => apiGet<ConsoleCase[]>('/episodes/cases'),
  })
  const systems = useQuery({
    queryKey: ['console-systems'],
    queryFn: () => apiGet<ConsoleSystem[]>('/episodes/systems'),
  })
  const schedule = useQuery({
    queryKey: ['console-schedule'],
    queryFn: () => apiGet<Schedule>('/episodes/schedule'),
  })
  const [mode, setMode] = useState<'case' | 'window'>('case')
  const [caseId, setCaseId] = useState('')
  const [system, setSystem] = useState('G3')
  const [runIdx, setRunIdx] = useState(1)
  const [interactive, setInteractive] = useState(false)
  const [episodeId, setEpisodeId] = useState<number | null>(null)
  const [ended, setEnded] = useState<number[]>([])
  const onEnd = useCallback((id: number) => setEnded((cur) => [...cur, id]), [])
  const ids = { case: useId(), system: useId(), run: useId(), inter: useId(), mode: useId() }

  const start = useMutation({
    mutationFn: () =>
      apiPost<{ episode_id: number }>('/episodes', {
        case_id: caseId,
        system,
        run_idx: runIdx,
        interactive_approval: interactive,
      }),
    onSuccess: (r) => setEpisodeId(r.episode_id),
  })
  const stop = useMutation({
    mutationFn: () => apiPost(`/episodes/${episodeId}/stop`, {}),
  })
  const running = episodeId !== null && !ended.includes(episodeId)
  const chosenCase = cases.data?.find((c) => c.case_id === caseId)
  const choices = (cases.data ?? []).filter((c) => (c.mode ?? 'case') === mode)

  return (
    <section aria-labelledby="console-title">
      <h1 id="console-title" className="mb-2 text-2xl font-semibold">
        Agent Console
      </h1>
      <p className="mb-4 text-sm">
        One live episode from the case&apos;s frozen prefix, or from a live investigation of a
        window (Exp 3): the agent proposes, the gate decides, tools run (state-changing actions are
        mocks). Console episodes belong to no run and are never used in experiments.
      </p>
      {[cases, systems, schedule].map((q, i) =>
        q.isError ? <ErrorBanner key={i} error={q.error} /> : null,
      )}
      <form
        className="mb-4 flex flex-wrap items-end gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          start.mutate()
        }}
      >
        <fieldset className="flex flex-col text-sm">
          <legend>Start from a case or a window</legend>
          {(
            [
              ['case', 'Frozen prefix (Exp 2)'],
              ['window', 'Live investigation (Exp 3)'],
            ] as const
          ).map(([m, text]) => (
            <label key={m} className="flex items-center gap-1">
              <input
                type="radio"
                name={ids.mode}
                value={m}
                checked={mode === m}
                onChange={() => {
                  setMode(m)
                  setCaseId('')
                }}
              />
              {text}
            </label>
          ))}
        </fieldset>
        <div className="flex flex-col text-sm">
          <label htmlFor={ids.case}>{mode === 'case' ? 'Case' : 'Window'}</label>
          <select
            id={ids.case}
            className="rounded border border-gray-500 p-1"
            value={caseId}
            onChange={(e) => setCaseId(e.target.value)}
          >
            <option value="">Choose a {mode}…</option>
            {choices.map((c) => (
              <option key={c.case_id} value={c.case_id}>
                {mode === 'window' ? `${c.window_id} (${c.case_id})` : c.case_id}
                {c.labelled ? '' : ' (labels pending)'}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col text-sm">
          <label htmlFor={ids.system}>Gate configuration</label>
          <select
            id={ids.system}
            className="rounded border border-gray-500 p-1"
            value={system}
            onChange={(e) => setSystem(e.target.value)}
          >
            {(systems.data ?? []).map((s) => (
              <option key={s.id} value={s.id} disabled={Boolean(s.unavailable)}>
                {s.id}
                {s.unavailable ? ` — ${s.unavailable}` : ''}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col text-sm">
          <label htmlFor={ids.run}>Run</label>
          <select
            id={ids.run}
            className="rounded border border-gray-500 p-1"
            value={runIdx}
            onChange={(e) => setRunIdx(Number(e.target.value))}
          >
            {Object.entries(schedule.data ?? { '1': { temperature: 0.2, seed: 11 } }).map(
              ([r, v]) => (
                <option key={r} value={r}>
                  run {r} (temperature {v.temperature}, seed {v.seed})
                </option>
              ),
            )}
          </select>
        </div>
        <div className="flex items-center gap-1 text-sm">
          <input
            id={ids.inter}
            type="checkbox"
            checked={interactive}
            onChange={(e) => setInteractive(e.target.checked)}
          />
          <label htmlFor={ids.inter}>
            Demo-interactive approval (a person answers; not used in experiments)
          </label>
        </div>
        <button
          type="submit"
          className="rounded bg-blue-800 px-4 py-1 text-white disabled:opacity-60"
          disabled={!caseId || running || start.isPending}
        >
          Start
        </button>
        <button
          type="button"
          className="rounded border border-gray-600 px-4 py-1 disabled:opacity-60"
          disabled={!running || stop.isPending}
          onClick={() => stop.mutate()}
        >
          Stop
        </button>
      </form>
      <div aria-live="polite">
        {start.isError && <ErrorBanner error={start.error} />}
        {stop.isError && <ErrorBanner error={stop.error} />}
      </div>
      {chosenCase && (
        <p className="mb-2 text-sm">
          Request: <q>{chosenCase.request.text}</q> ({chosenCase.request.objective}{' '}
          {JSON.stringify(chosenCase.request.target)})
        </p>
      )}
      {episodeId !== null && <EpisodeView key={episodeId} id={episodeId} onEnd={onEnd} />}
    </section>
  )
}

function EpisodeView({ id, onEnd }: { id: number; onEnd: (id: number) => void }) {
  const { steps, pending, done } = useEpisode(id, onEnd)
  const answer = useMutation({
    mutationFn: (decision: 'grant' | 'deny') => apiPost(`/episodes/${id}/approval`, { decision }),
  })
  return (
    <>
      <h2 className="mb-1 text-lg font-semibold">
        Episode {id}
        {done ? '' : ' (running)'}
      </h2>
      <div className="mb-2">
        <RecoveryMeter steps={steps} />
      </div>
      {pending && (
        <div
          role="alert"
          className="mb-2 rounded border border-amber-700 bg-amber-50 p-2 text-sm text-amber-950"
        >
          <p className="font-semibold">
            Approval requested (demo-interactive, not used in experiments): {pending.call}
          </p>
          <div className="mt-1 flex gap-2">
            {(['grant', 'deny'] as const).map((d) => (
              <button
                key={d}
                type="button"
                className="rounded border border-gray-700 px-3 py-1 capitalize"
                onClick={() => answer.mutate(d)}
                disabled={answer.isPending}
              >
                {d}
              </button>
            ))}
          </div>
          {answer.isError && <ErrorBanner error={answer.error} />}
        </div>
      )}
      {steps.length > 0 && isInvestigation(steps[0]) && (
        <p className="mb-2 text-sm" aria-live="polite">
          {done
            ? 'Investigation, then response.'
            : steps.every(isInvestigation)
              ? 'Phase: investigating the window…'
              : 'Phase: responding from the investigation’s handover…'}
        </p>
      )}
      <Timeline steps={steps} />
      {done?.investigation && <InvestigationPanel inv={done.investigation} />}
      {done && <OutcomePanel ep={done} />}
    </>
  )
}

/** What the live investigation found (Exp 3): accepted techniques with their cited records, and
 * every hypothesis with the technique verifier's verdict. */
export function InvestigationPanel({ inv }: { inv: Investigation }) {
  return (
    <section
      aria-label="Investigation findings"
      className="mt-3 rounded border border-violet-700 p-2 text-sm"
    >
      <p>
        Predicted techniques:{' '}
        <strong>
          {inv.predicted_techniques.length ? inv.predicted_techniques.join(', ') : 'none'}
        </strong>{' '}
        ({inv.llm_calls} investigation calls
        {inv.budget_exhausted ? '; the investigation budget was reached' : ''})
      </p>
      <ul className="mt-1 list-disc pl-5">
        {inv.hypotheses.map((h) => (
          <li key={h.technique_id}>
            <span className="font-mono">{h.technique_id}</span>: {h.verdict ?? 'no verdict'}
            {h.cited.length > 0 && <> (cited {h.cited.join(', ')})</>} — {h.reason}
          </li>
        ))}
      </ul>
      {inv.handover_stubbed.length > 0 && (
        <p className="mt-1">
          {inv.handover_stubbed.length} query result(s) did not fit the handover and were passed as
          record-ID stubs.
        </p>
      )}
    </section>
  )
}

export function OutcomePanel({ ep }: { ep: EpisodeSummary }) {
  return (
    <div role="status" className="mt-3 rounded border border-gray-500 p-2 text-sm">
      {ep.status === 'done' ? (
        <>
          <p>
            Ended: <strong>{ep.terminal_state}</strong>
            {ep.budget_exhausted && ' (a cap was reached: flag, not an outcome)'}
          </p>
          <p>
            Outcome:{' '}
            {ep.outcome ? (
              <>
                <strong>{ep.outcome.replaceAll('_', ' ')}</strong> — {ep.rule_text}
                {ep.unqualified_escalation && ' Flag: a non-qualifying escalation was made.'}
              </>
            ) : (
              'not scored (the case’s labels are pending)'
            )}
          </p>
        </>
      ) : (
        <p>Episode ended without a terminal state: {ep.error ?? 'error'}</p>
      )}
      <p>
        <Link className="text-blue-800 underline" to={`/traces/${ep.id}`}>
          Open the trace
        </Link>
      </p>
    </div>
  )
}
