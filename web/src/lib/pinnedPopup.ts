// Which pinned announcement pops up (pure, unit-tested). A visitor sees the newest published pinned
// announcement they have not dismissed yet; dismissing it remembers every pinned id shown at that moment,
// so older pinned notices do not pop up one after another. A newer pinned announcement pops up again.
import type { Announcement } from './data'

export const PINNED_SEEN_KEY = 'sac.pinned-announcements.seen'
const KEEP = 50

export function parseSeen(raw: string | null): Set<string> {
  try {
    const value = JSON.parse(raw ?? '[]')
    return new Set(Array.isArray(value) ? value.map(String) : [])
  } catch { return new Set() }
}

type Row = Pick<Announcement, 'id' | 'is_pinned' | 'is_published' | 'created_at'>

/** Published pinned announcements, newest first. */
export function pinnedRows<T extends Row>(rows: T[]): T[] {
  return rows.filter(row => row.is_pinned && row.is_published !== false)
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
}

export function pickPinnedPopup<T extends Row>(rows: T[], seen: Set<string>): T | null {
  return pinnedRows(rows).find(row => !seen.has(String(row.id))) ?? null
}

/** The stored value after dismissing: earlier ids plus the given ones, the most recent last, capped. */
export function rememberSeen(raw: string | null, ids: Array<string | number>): string {
  const merged = [...parseSeen(raw)].filter(id => !ids.map(String).includes(id)).concat(ids.map(String))
  return JSON.stringify(merged.slice(-KEEP))
}
