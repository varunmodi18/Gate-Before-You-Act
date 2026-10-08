import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table'
import { useMemo } from 'react'

import type { Cell } from '../api/client'
import { formatCell } from './format'

/** Plain, accessible table over column/row arrays (P1 scope: no custom styling). */
export function DataTable({
  columns,
  rows,
  caption,
  onRowClick,
  rowLabel,
  renderCell,
}: {
  columns: string[]
  rows: Cell[][]
  caption: string
  onRowClick?: (row: Cell[]) => void
  rowLabel?: (row: Cell[]) => string
  renderCell?: (column: string, value: Cell, row: Cell[]) => React.ReactNode
}) {
  const defs = useMemo<ColumnDef<Cell[]>[]>(
    () =>
      columns.map((name, i) => ({
        id: name,
        header: name,
        accessorFn: (row) => row[i],
        cell: (info) => {
          const value = info.getValue() as Cell
          return renderCell ? renderCell(name, value, info.row.original) : formatCell(value)
        },
      })),
    [columns, renderCell],
  )
  // TanStack Table's hook returns functions React Compiler cannot memoise; safe here.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({ data: rows, columns: defs, getCoreRowModel: getCoreRowModel() })

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {onRowClick && (
                <th scope="col" className="border-b border-gray-400 px-2 py-1 text-left">
                  <span className="sr-only">Open</span>
                </th>
              )}
              {hg.headers.map((h) => (
                <th
                  key={h.id}
                  scope="col"
                  className="border-b border-gray-400 px-2 py-1 text-left font-semibold"
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="border-b border-gray-200 align-top">
              {onRowClick && (
                <td className="px-2 py-1">
                  <button
                    type="button"
                    className="rounded border border-gray-500 px-2 py-0.5"
                    onClick={() => onRowClick(row.original)}
                    aria-label={rowLabel ? rowLabel(row.original) : 'Open row'}
                  >
                    Open
                  </button>
                </td>
              )}
              {row.getVisibleCells().map((cell) => (
                <td
                  key={cell.id}
                  className="max-w-md min-w-[7rem] px-2 py-1 [overflow-wrap:anywhere]"
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
