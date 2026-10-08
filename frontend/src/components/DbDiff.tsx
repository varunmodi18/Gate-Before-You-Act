import type { DbDiffJson } from '../api/client'
import { formatCell } from './format'

/** Per-case database against the window: removed, added and changed records (E3-E5). */
export function DbDiff({ diff }: { diff: DbDiffJson }) {
  return (
    <section aria-labelledby="diff-title" className="mt-3">
      <h3 id="diff-title" className="font-semibold">
        Database diff against the window
      </h3>
      <p className="text-sm">
        Removed: {diff.removed.length ? diff.removed.join(', ') : 'none'}; added:{' '}
        {diff.added.length
          ? diff.added.map((a) => `${a.record_id} (${a.table})`).join(', ')
          : 'none'}
        ; changed: {diff.changed.length}
      </p>
      {diff.changed.length > 0 && (
        <table className="mt-1 min-w-full border-collapse text-sm">
          <caption className="sr-only">Changed fields per record</caption>
          <thead>
            <tr>
              {['Record', 'Table', 'Field', 'Before', 'After'].map((h) => (
                <th key={h} scope="col" className="border-b border-gray-400 px-2 py-1 text-left">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {diff.changed.flatMap((c) =>
              Object.entries(c.fields).map(([f, [b, a]]) => (
                <tr key={`${c.record_id}-${f}`} className="border-b border-gray-200">
                  <td className="px-2 py-1">{c.record_id}</td>
                  <td className="px-2 py-1">{c.table}</td>
                  <td className="px-2 py-1 font-mono">{f}</td>
                  <td className="px-2 py-1 font-mono">{formatCell(b)}</td>
                  <td className="px-2 py-1 font-mono">{formatCell(a)}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      )}
    </section>
  )
}
