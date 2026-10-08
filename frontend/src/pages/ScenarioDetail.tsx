import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router'

import {
  ApiError,
  apiGet,
  apiPost,
  apiPut,
  type CaseDetail,
  type ScenarioDetail as Detail,
  type ScenarioJson,
  type ValidationReport,
} from '../api/client'
import { ContextEditor, JsonField } from '../components/ContextEditor'
import { DbDiff } from '../components/DbDiff'
import { ErrorBanner } from '../components/ErrorBanner'
import { SqlConsole } from '../components/SqlConsole'
import { VariantGrid } from '../components/VariantGrid'

const input = 'rounded border border-gray-500 p-1 text-sm'
const ids = (text: string) =>
  text
    .split(/[\s,]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0)

/** Scenario Studio (plan §E.1 row 3, T4.5): edit, pick E1 citations from query results,
 * generate the variants, validate, inspect cases and their database diffs. */
export function ScenarioDetail() {
  const { sid = '' } = useParams()
  const detail = useQuery({
    queryKey: ['scenario', sid],
    queryFn: () => apiGet<Detail>(`/scenarios/${sid}`),
  })
  // Lives here so that it survives the editor's remount after a save.
  const [notice, setNotice] = useState<string | null>(null)
  if (detail.isError) return <ErrorBanner error={detail.error} />
  if (!detail.data) return <p>Loading…</p>
  return (
    <Editor
      key={JSON.stringify(detail.data.scenario)}
      detail={detail.data}
      notice={notice}
      onNotice={setNotice}
    />
  )
}

function Editor({
  detail,
  notice,
  onNotice,
}: {
  detail: Detail
  notice: string | null
  onNotice: (text: string | null) => void
}) {
  const qc = useQueryClient()
  const sid = detail.id
  const [draft, setDraft] = useState<ScenarioJson>(detail.scenario)
  const [jsonView, setJsonView] = useState(false)
  const [picked, setPicked] = useState<number | null>(null)
  const [variant, setVariant] = useState<string | null>(null)
  const set = (patch: Partial<ScenarioJson>) => setDraft((d) => ({ ...d, ...patch }))
  const setE1 = (patch: Partial<ScenarioJson['e1']>) => set({ e1: { ...draft.e1, ...patch } })

  const save = useMutation({
    mutationFn: () => apiPut<{ scenario: ScenarioJson }>(`/scenarios/${sid}`, draft),
    onSuccess: () => {
      onNotice('✓ Saved.')
      void qc.invalidateQueries({ queryKey: ['scenario', sid] })
    },
  })
  const generate = useMutation({
    mutationFn: () => apiPost<{ variants: string[] }>(`/scenarios/${sid}/generate`, {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['scenario', sid] }),
  })
  const validate = useMutation({
    mutationFn: () => apiPost<ValidationReport>(`/scenarios/${sid}/validate`, {}),
  })
  const caseQ = useQuery({
    queryKey: ['case-detail', sid, variant, generate.data],
    queryFn: () => apiGet<CaseDetail>(`/scenarios/${sid}/cases/${variant}`),
    enabled: variant !== null,
  })
  const dirty = JSON.stringify(draft) !== JSON.stringify(detail.scenario)
  const details =
    save.error instanceof ApiError
      ? (save.error.body.details?.errors as { loc: unknown[]; msg: string }[] | undefined)
      : undefined

  return (
    <section aria-labelledby="sd-title">
      <h1 id="sd-title" className="mb-1 text-2xl font-semibold">
        Scenario {sid}
      </h1>
      <p className="mb-3 text-sm">
        Window <span className="font-mono">{draft.window_id}</span>; status {detail.status}
        {dirty && ' — unsaved changes'}
      </p>

      <div className="mb-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded bg-blue-800 px-3 py-1 text-white disabled:opacity-60"
          disabled={save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? 'Saving…' : 'Save scenario'}
        </button>
        <button
          type="button"
          className="rounded border border-gray-600 px-3 py-1 disabled:opacity-60"
          disabled={dirty || generate.isPending}
          onClick={() => generate.mutate()}
        >
          {generate.isPending ? 'Generating…' : 'Generate variants'}
        </button>
        <button
          type="button"
          className="rounded border border-gray-600 px-3 py-1 disabled:opacity-60"
          disabled={dirty || validate.isPending}
          onClick={() => validate.mutate()}
        >
          {validate.isPending ? 'Validating…' : 'Validate'}
        </button>
        <button
          type="button"
          className="rounded border border-gray-600 px-3 py-1"
          onClick={() => setJsonView((v) => !v)}
        >
          {jsonView ? 'Show the form' : 'Edit as JSON'}
        </button>
      </div>
      <div aria-live="polite">
        {save.isError && <ErrorBanner error={save.error} />}
        {details && (
          <ul className="mb-2 list-disc pl-6 text-sm text-red-900">
            {details.map((d, i) => (
              <li key={i}>
                {d.loc.join('.')}: {d.msg}
              </li>
            ))}
          </ul>
        )}
        {notice && !dirty && <p className="text-sm">{notice}</p>}
        {generate.isError && <ErrorBanner error={generate.error} />}
        {generate.data && (
          <p className="text-sm">✓ Generated: {generate.data.variants.join(', ')}</p>
        )}
        {validate.isError && <ErrorBanner error={validate.error} />}
      </div>

      {jsonView ? (
        <JsonField
          label="scenario.json"
          rows={30}
          value={draft}
          onChange={(v) => setDraft(v as ScenarioJson)}
        />
      ) : (
        <>
          <fieldset className="mb-3 grid max-w-4xl grid-cols-1 gap-2 rounded border border-gray-400 p-3 md:grid-cols-2">
            <legend className="font-semibold">Request and E1 package</legend>
            <label className="flex flex-col text-sm">
              Target host
              <input
                className={input}
                value={draft.target_host}
                onChange={(e) => set({ target_host: e.target.value })}
              />
            </label>
            <label className="flex flex-col text-sm">
              Request text
              <input
                className={input}
                value={draft.request.text}
                onChange={(e) => set({ request: { ...draft.request, text: e.target.value } })}
              />
            </label>
            <label className="flex flex-col text-sm">
              E1 cited record ids
              <input
                className={`${input} font-mono`}
                value={draft.e1.cited.join(', ')}
                onChange={(e) => setE1({ cited: ids(e.target.value) })}
              />
            </label>
            <label className="flex flex-col text-sm">
              Suspicious record ids (the activity)
              <input
                className={`${input} font-mono`}
                value={draft.suspicious_record_ids.join(', ')}
                onChange={(e) => set({ suspicious_record_ids: ids(e.target.value) })}
              />
            </label>
            <label className="flex flex-col text-sm">
              Claimed technique (the agent&apos;s claim)
              <input
                className={input}
                value={draft.e1.technique_claimed ?? ''}
                onChange={(e) => setE1({ technique_claimed: e.target.value || null })}
              />
            </label>
            <label className="flex flex-col text-sm">
              Rationale (shown to A3 only)
              <input
                className={input}
                value={draft.e1.rationale ?? ''}
                onChange={(e) => setE1({ rationale: e.target.value || null })}
              />
            </label>
            <p className="text-xs text-gray-700 md:col-span-2">
              Objective, E2-E5 parameters and the Set R edit are edited in the JSON view.
            </p>
          </fieldset>
          <ContextEditor
            value={draft.trusted_context}
            onChange={(ctx) => set({ trusted_context: ctx })}
          />
          <SqlConsole windowId={draft.window_id} onOpenRecord={setPicked} />
          {picked !== null && (
            <div role="group" aria-label={`Record ${picked}`} className="mt-2 flex gap-2 text-sm">
              <span>Record {picked}:</span>
              <button
                type="button"
                className="underline"
                onClick={() => setE1({ cited: [...new Set([...draft.e1.cited, picked])] })}
              >
                Add to E1 citations
              </button>
              <button
                type="button"
                className="underline"
                onClick={() =>
                  set({
                    suspicious_record_ids: [...new Set([...draft.suspicious_record_ids, picked])],
                  })
                }
              >
                Add to suspicious
              </button>
            </div>
          )}
        </>
      )}

      {validate.data && (
        <section aria-labelledby="val-title" className="mt-4">
          <h2 id="val-title" className="text-lg font-semibold">
            Validation
          </h2>
          <VariantGrid report={validate.data} />
        </section>
      )}

      <section aria-labelledby="cases-title" className="mt-4">
        <h2 id="cases-title" className="text-lg font-semibold">
          Cases
        </h2>
        {detail.cases.length === 0 && <p className="text-sm">Not generated yet.</p>}
        <div className="flex flex-wrap gap-2">
          {detail.cases.map((c) => (
            <button
              key={c.id}
              type="button"
              aria-pressed={variant === c.variant}
              className="rounded border border-gray-600 px-2 py-1 text-sm"
              onClick={() => setVariant(c.variant)}
            >
              {c.variant} ({c.cited.join(', ')}){c.labelled ? ' labelled' : ''}
            </button>
          ))}
        </div>
        {caseQ.isError && <ErrorBanner error={caseQ.error} />}
        {caseQ.data && (
          <div className="mt-2 text-sm">
            <p>
              <span className="font-mono">{caseQ.data.case.package.tool}</span>(
              {JSON.stringify(caseQ.data.case.package.args)}) citing [
              {caseQ.data.case.package.cited.join(', ')}]; database{' '}
              <span className="font-mono">{caseQ.data.db}</span>
            </p>
            {caseQ.data.diff && <DbDiff diff={caseQ.data.diff} />}
            {caseQ.data.prefix && (
              <details className="mt-2">
                <summary className="cursor-pointer font-semibold">
                  Prefix: {caseQ.data.prefix.queries.length} queries, retrieved [
                  {caseQ.data.prefix.retrieved.join(', ')}]
                </summary>
                {caseQ.data.prefix.queries.map((q, i) => (
                  <div key={i} className="mt-2">
                    <code className="block">{q.sql}</code>
                    <pre
                      tabIndex={0}
                      aria-label={`Prefix query ${i + 1} result`}
                      className="max-h-64 overflow-auto whitespace-pre-wrap break-all bg-gray-100 p-2 text-xs"
                    >
                      {q.result}
                    </pre>
                  </div>
                ))}
              </details>
            )}
          </div>
        )}
      </section>
    </section>
  )
}
