// Thin JSON client for /api/v1. Errors follow the envelope of plan §F.6.

export const API_BASE = '/api/v1'

export interface ApiErrorBody {
  code: string
  message: string
  hint?: string | null
  details?: Record<string, unknown>
}

export class ApiError extends Error {
  readonly status: number
  readonly body: ApiErrorBody

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.status = status
    this.body = body
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, { headers: { Accept: 'application/json' } })
  if (!resp.ok) {
    let body: ApiErrorBody = { code: 'HTTP_ERROR', message: `HTTP ${resp.status}` }
    try {
      const json = (await resp.json()) as { error?: ApiErrorBody }
      if (json.error) body = json.error
    } catch {
      // Non-JSON error body: keep the generic message.
    }
    throw new ApiError(resp.status, body)
  }
  return (await resp.json()) as T
}

export interface Health {
  api: string
}
