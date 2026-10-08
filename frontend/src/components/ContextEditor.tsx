import { useState } from 'react'

import type { Asset, Ticket, TrustedContextJson } from '../api/client'

const input = 'rounded border border-gray-500 p-1 text-sm'

/** Trusted-context form (plan §E.1 row 3): assets, change tickets and the approval script as
 * tables; identities and network as JSON (P1: plainest form). The server validates on save. */
export function ContextEditor({
  value,
  onChange,
}: {
  value: TrustedContextJson
  onChange: (ctx: TrustedContextJson) => void
}) {
  const set = (patch: Partial<TrustedContextJson>) => onChange({ ...value, ...patch })
  const setAsset = (i: number, patch: Partial<Asset>) =>
    set({ assets: value.assets.map((a, j) => (j === i ? { ...a, ...patch } : a)) })
  const setTicket = (i: number, patch: Partial<Ticket>) =>
    set({ change_tickets: value.change_tickets.map((t, j) => (j === i ? { ...t, ...patch } : t)) })

  return (
    <fieldset className="rounded border border-gray-400 p-3">
      <legend className="font-semibold">Trusted context</legend>

      <table className="text-sm">
        <caption className="text-left font-semibold">Assets</caption>
        <thead>
          <tr>
            {['Host', 'Role', 'Tier', ''].map((h, i) => (
              <th key={i} scope="col" className="px-1 text-left">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {value.assets.map((a, i) => (
            <tr key={i}>
              <td className="px-1">
                <input
                  aria-label={`Asset ${i + 1} host`}
                  className={input}
                  value={a.host}
                  onChange={(e) => setAsset(i, { host: e.target.value })}
                />
              </td>
              <td className="px-1">
                <input
                  aria-label={`Asset ${i + 1} role`}
                  className={input}
                  value={a.role}
                  onChange={(e) => setAsset(i, { role: e.target.value })}
                />
              </td>
              <td className="px-1">
                <select
                  aria-label={`Asset ${i + 1} tier`}
                  className={input}
                  value={a.tier}
                  onChange={(e) => setAsset(i, { tier: Number(e.target.value) })}
                >
                  {[0, 1, 2].map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </td>
              <td className="px-1">
                <button
                  type="button"
                  className="text-sm underline"
                  onClick={() => set({ assets: value.assets.filter((_, j) => j !== i) })}
                >
                  Remove asset {i + 1}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button
        type="button"
        className="mt-1 text-sm underline"
        onClick={() =>
          set({ assets: [...value.assets, { host: '', role: 'workstation', tier: 2 }] })
        }
      >
        Add asset
      </button>

      <table className="mt-3 text-sm">
        <caption className="text-left font-semibold">Change tickets</caption>
        <thead>
          <tr>
            {[
              'Id',
              'Host',
              'Account',
              'Command pattern (regex)',
              'Start (UTC)',
              'End (UTC)',
              'Approved',
              '',
            ].map((h, i) => (
              <th key={i} scope="col" className="px-1 text-left">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {value.change_tickets.map((t, i) => (
            <tr key={i}>
              {(['id', 'host', 'account', 'command_pattern', 'start', 'end'] as const).map((k) => (
                <td key={k} className="px-1">
                  <input
                    aria-label={`Ticket ${i + 1} ${k}`}
                    className={input}
                    value={t[k]}
                    onChange={(e) => setTicket(i, { [k]: e.target.value })}
                  />
                </td>
              ))}
              <td className="px-1">
                <input
                  type="checkbox"
                  aria-label={`Ticket ${i + 1} approved`}
                  checked={t.approved}
                  onChange={(e) => setTicket(i, { approved: e.target.checked })}
                />
              </td>
              <td className="px-1">
                <button
                  type="button"
                  className="text-sm underline"
                  onClick={() =>
                    set({ change_tickets: value.change_tickets.filter((_, j) => j !== i) })
                  }
                >
                  Remove ticket {i + 1}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button
        type="button"
        className="mt-1 text-sm underline"
        onClick={() =>
          set({
            change_tickets: [
              ...value.change_tickets,
              {
                id: `CHG-${value.change_tickets.length + 1}`,
                host: value.assets[0]?.host ?? '',
                account: '',
                command_pattern: '',
                start: '2020-01-01T00:00:00Z',
                end: '2020-01-01T01:00:00Z',
                approved: true,
              },
            ],
          })
        }
      >
        Add ticket
      </button>

      <label className="mt-3 flex max-w-xs flex-col text-sm">
        Approval script
        <select
          className={input}
          value={value.approval_script.mode}
          onChange={(e) => set({ approval_script: { mode: e.target.value } })}
        >
          {['unreachable', 'grant', 'deny'].map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </label>

      <JsonField
        label="Identities (JSON list)"
        value={value.identities}
        onChange={(v) => set({ identities: v as TrustedContextJson['identities'] })}
      />
      <JsonField
        label="Network (JSON)"
        value={value.network}
        onChange={(v) => set({ network: v as TrustedContextJson['network'] })}
      />
    </fieldset>
  )
}

/** A JSON text area that reports a parse error instead of losing the edit. */
export function JsonField({
  label,
  value,
  onChange,
  rows = 4,
}: {
  label: string
  value: unknown
  onChange: (v: unknown) => void
  rows?: number
}) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2))
  const [error, setError] = useState<string | null>(null)
  return (
    <label className="mt-3 flex flex-col text-sm">
      {label}
      <textarea
        className={`${input} font-mono`}
        rows={rows}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          try {
            onChange(JSON.parse(e.target.value))
            setError(null)
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err))
          }
        }}
      />
      {error && (
        <span role="alert" className="text-red-800">
          ✕ Invalid JSON: {error}
        </span>
      )}
    </label>
  )
}
