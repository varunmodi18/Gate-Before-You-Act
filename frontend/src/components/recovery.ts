import type { CheckResult, StepOut } from '../api/client'

type P = Record<string, unknown>

/** Recovery budget after the first INSUFFICIENT (§D.6.2a): granted, used, refused. */
export function recoveryState(steps: StepOut[]) {
  let granted: number | null = null
  let used = 0
  let refused = 0
  for (const s of steps) {
    if (s.kind === 'gate' && granted === null) {
      const checks = ((s.payload.decision as P | undefined)?.checks ?? []) as CheckResult[]
      const c4 = checks.find((c) => c.code === 'C4_INSUFFICIENT')
      if (c4) granted = Number(c4.details.recovery_queries_left ?? 0)
    } else if (s.kind === 'tool' && granted !== null && s.payload.tool === 'sql_query') {
      if (s.payload.error_code === 'RECOVERY_BUDGET_EXHAUSTED') refused += 1
      else used += 1
    }
  }
  return { granted, used, refused }
}

/** A step of the live investigation that precedes an Exp 3 response (T6.3). */
export const isInvestigation = (s: StepOut) => s.payload.phase === 'investigation'
