/** Kimi Coding Plan codes: one per team that has a successful practice score, claimed by the captain. */
export const KIMI_PLAN_PROVIDER = 'kimi'

export interface KimiPlanStatus {
  has_team: boolean
  is_captain: boolean
  hidden: boolean
  qualified: boolean
  eligible: boolean
  imported: boolean
  available: number
  code: string | null
  note: string
  claimed_at: string | null
  claimed_by: string | null
}

/**
 * What the dashboard shows:
 * - claimed: the team's code (every member sees it)
 * - claimable: captain of an eligible team, codes in the pool
 * - wait_captain: eligible team, codes in the pool, but only the captain can claim
 * - coming_soon: no codes imported yet (eligibility still shown)
 * - sold_out: codes were imported and all are taken (first 100 teams, first come, first served); no popup, no
 *   claim button, just a calm notice, whatever the team's eligibility
 * - not_eligible: the team still needs one successful practice score (or is a hidden/test team)
 */
export type KimiPlanState = 'no_team' | 'claimed' | 'claimable' | 'wait_captain' | 'coming_soon' | 'sold_out' | 'not_eligible'

export function normalizeKimiPlanStatus(raw: unknown): KimiPlanStatus {
  const row = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  return {
    has_team: Boolean(row.has_team),
    is_captain: Boolean(row.is_captain),
    hidden: Boolean(row.hidden),
    qualified: Boolean(row.qualified),
    eligible: Boolean(row.eligible),
    imported: Boolean(row.imported),
    available: Number(row.available ?? 0) || 0,
    code: typeof row.code === 'string' && row.code ? row.code : null,
    note: typeof row.note === 'string' ? row.note : '',
    claimed_at: typeof row.claimed_at === 'string' ? row.claimed_at : null,
    claimed_by: typeof row.claimed_by === 'string' && row.claimed_by ? row.claimed_by : null,
  }
}

export function kimiPlanState(s: KimiPlanStatus): KimiPlanState {
  if (!s.has_team) return 'no_team'
  if (s.code) return 'claimed'
  if (!s.imported) return 'coming_soon'
  if (s.available <= 0) return 'sold_out'
  if (!s.eligible) return 'not_eligible'
  return s.is_captain ? 'claimable' : 'wait_captain'
}

/** Who the "your team can claim" popup speaks to, or null when it must not show (claimed, sold out, not eligible...). */
export function kimiPlanPopupRole(s: KimiPlanStatus): 'captain' | 'member' | null {
  const state = kimiPlanState(s)
  return state === 'claimable' ? 'captain' : state === 'wait_captain' ? 'member' : null
}

/**
 * The pool can run out between loading the status and pressing claim. The backend then raises `no_codes_left`;
 * the panel switches to the sold-out notice instead of showing an error. Returns null for any other error.
 */
export function kimiPlanSoldOutAfterClaim(s: KimiPlanStatus, message: string): KimiPlanStatus | null {
  return message.trim() === 'no_codes_left' ? { ...s, imported: true, available: 0 } : null
}
