import * as Dialog from '@radix-ui/react-dialog'
import { useQuery } from '@tanstack/react-query'

import { apiGet, type RecordDetail } from '../api/client'
import { ErrorBanner } from './ErrorBanner'

/** Record detail: the normalised row and the original event JSON (untrusted log data). */
export function RecordDrawer({
  windowId,
  recordId,
  onClose,
}: {
  windowId: string
  recordId: number | null
  onClose: () => void
}) {
  const open = recordId !== null
  const detail = useQuery({
    queryKey: ['record', windowId, recordId],
    queryFn: () => apiGet<RecordDetail>(`/windows/${windowId}/records/${recordId}`),
    enabled: open,
  })

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40" />
        <Dialog.Content
          className="fixed top-0 right-0 h-full w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-xl"
          aria-describedby={undefined}
        >
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-xl font-semibold">Record {recordId}</Dialog.Title>
            <Dialog.Close className="rounded border border-gray-500 px-3 py-1">Close</Dialog.Close>
          </div>
          {detail.isPending && <p>Loading…</p>}
          {detail.isError && <ErrorBanner error={detail.error} />}
          {detail.data && (
            <>
              <h3 className="mt-2 font-semibold">
                Normalised{detail.data.table ? ` (${detail.data.table})` : ''}
              </h3>
              {detail.data.normalised ? (
                <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
                  {Object.entries(detail.data.normalised).map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="font-mono text-gray-700">{k}</dt>
                      <dd className="font-mono break-all">{v === null ? '—' : String(v)}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p>Not mapped to a normalised table; kept in raw_events only.</p>
              )}
              <h3 className="mt-4 font-semibold">
                Raw event <span className="text-sm font-normal">(untrusted log data)</span>
              </h3>
              <pre className="mt-1 overflow-x-auto rounded bg-gray-100 p-2 text-xs">
                {JSON.stringify(detail.data.raw, null, 2)}
              </pre>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
