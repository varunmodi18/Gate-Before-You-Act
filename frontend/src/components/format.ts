import type { Cell } from '../api/client'

/** Display a cell; very long values are shortened for the table (full value in the record drawer). */
export function formatCell(value: Cell): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string' && value.length > 160)
    return `${value.slice(0, 160)}… (${value.length} chars)`
  return String(value)
}
