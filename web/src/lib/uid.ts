// The participant's permanent 9-digit UID (assigned by the database, returned only by me()) and the
// small bottom-right label that shows it. Pure helpers, so gating and placement are unit-tested.

/** The label text, or null when there is nothing valid to show (logged out, not loaded, malformed). */
export function uidLabel(loggedIn: boolean, uid: unknown): string | null {
  if (!loggedIn) return null
  const n = typeof uid === 'string' && /^\d{9}$/.test(uid) ? Number(uid) : uid
  if (typeof n !== 'number' || !Number.isInteger(n) || n < 100000001 || n > 999999999) return null
  return `UID ${n}`
}

export interface Box { left: number; top: number; right: number; bottom: number }

/** True when two boxes overlap, counting `gap` px around the first one as part of it. */
export function boxesOverlap(a: Box, b: Box, gap = 0): boolean {
  if (b.right <= b.left || b.bottom <= b.top) return false // hidden or empty
  return a.left - gap < b.right && b.left < a.right + gap && a.top - gap < b.bottom && b.top < a.bottom + gap
}

/** Things the label must never sit on: the phone register bar, the homepage section rail, the sky console, open popups. */
export const UID_AVOID = '.register-bar, .section-rail, [data-testid="sky-console"], [role="dialog"]:not(.uid-egg), dialog[open]:not(.uid-egg), .flash-item'

/** Two taps within this window on the same spot count as a double tap. */
export const DOUBLE_TAP_MS = 350

export function isDoubleTap(previous: { t: number; x: number; y: number } | null, t: number, x: number, y: number): boolean {
  return !!previous && t - previous.t <= DOUBLE_TAP_MS && Math.hypot(x - previous.x, y - previous.y) < 30
}
