import { ApiError } from '../api/client'

/** Typed error banner using the §F.6 envelope (code, message, hint). */
export function ErrorBanner({ error }: { error: unknown }) {
  const body =
    error instanceof ApiError
      ? error.body
      : { code: 'ERROR', message: error instanceof Error ? error.message : String(error) }
  return (
    <div role="alert" className="my-2 rounded border border-red-700 bg-red-50 p-3 text-red-900">
      <p>
        <span aria-hidden="true">✕ </span>
        <strong>{body.code}</strong>: {body.message}
      </p>
      {'hint' in body && body.hint && <p className="mt-1">Hint: {body.hint}</p>}
    </div>
  )
}
