// Popup rules (pure, unit-tested). Every site popup follows the same three rules:
//  1. Each person sees each popup at most once. A popup is identified by a content key, so it shows again only
//     when it is meant to (Kimi plan team + role, quota reset id, inbox row id).
//     Pinned announcements are the exception: they come back once per (Beijing) day until the person ticks
//     "don't show this again" (key id + notify_version; an organizer's "remind everyone" bumps the version,
//     editing the text never re-shows it).
//  2. Showing it counts as seen: closing it in any way (button, Esc, outside click, navigating away) or keeping it
//     on screen for SEEN_AFTER_MS marks it seen, so a reload never brings it back.
//  3. At most one popup per page load: the candidates that ask within SETTLE_MS are compared and the most important
//     one shows; the rest wait for a later visit.
// "Seen" lives in localStorage and, for signed-in users, on the server (my_seen_popups / mark_popups_seen).

export const POPUP_SEEN_KEY = 'sac.popups.seen'
export const SEEN_AFTER_MS = 3000
export const SETTLE_MS = 1500
const KEEP = 300

/** Lower is more important. Unknown names come last. */
export const POPUP_PRIORITY: Record<string, number> = {
  'browser-notice': 0,
  'team-requests': 1,
  'quota-reset': 2,
  'pinned-announcement': 3,
  'kimi-plan': 4,
  'compete-guide': 5,
}

/** The one popup this page load shows, among the names that asked (in asking order). */
export function pickPopup(candidates: string[]): string | null {
  let best: string | null = null
  for (const name of candidates) {
    if (best === null || (POPUP_PRIORITY[name] ?? 99) < (POPUP_PRIORITY[best] ?? 99)) best = name
  }
  return best
}

/** FNV-1a (32-bit), hex: a short stable fingerprint of a popup's text. */
export function contentHash(...parts: Array<string | null | undefined>): string {
  let h = 0x811c9dc5
  const text = parts.map(p => p ?? '').join('\u0000')
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

type AnnouncementRef = { id: string | number; notify_version?: number | null }
const annVersion = (a: AnnouncementRef) => Math.max(1, Math.floor(Number(a.notify_version) || 1))
const annId = (a: AnnouncementRef) => String(a.id).replace(/[^0-9A-Za-z-]/g, '')
/** Before snoozing existed: closed once (any way). Such a record now counts as a snooze, not a dismissal. */
export const announcementKey = (a: AnnouncementRef) => `ann:${annId(a)}:v${annVersion(a)}`
/** "Don't show this again" ticked for this notify_version. */
export const announcementOffKey = (a: AnnouncementRef) => `ann-off:${annId(a)}:v${annVersion(a)}`
/** Closed without the tick on `day` (Beijing date): not shown again that day. */
export const announcementSnoozeKey = (a: AnnouncementRef, day: string) => `ann-snooze:${annId(a)}:v${annVersion(a)}:${day}`

/** The Beijing (UTC+8) calendar date, YYYY-MM-DD: snoozes end at Beijing midnight. */
export function beijingDay(now = Date.now()): string {
  return new Date(now + 8 * 3600_000).toISOString().slice(0, 10)
}

/** Closed before snoozing existed (old seen keys, incl. text-hash keys for version 1). */
export function announcementLegacySeen(a: AnnouncementRef, seen: Set<string>): boolean {
  if (seen.has(announcementKey(a))) return true
  if (annVersion(a) !== 1) return false
  const legacy = new RegExp(`^ann:${annId(a)}:[0-9a-f]{8}$`)
  for (const k of seen) if (legacy.test(k)) return true
  return false
}

/** A legacy close that has not been turned into a snooze yet (it then counts as snoozed for today, once). */
export function needsLegacySnooze(a: AnnouncementRef, seen: Set<string>, legacyIds: Set<string> = new Set()): boolean {
  if (seen.has(announcementOffKey(a))) return false
  if (!announcementLegacySeen(a, seen) && !(annVersion(a) === 1 && legacyIds.has(String(a.id)))) return false
  const prefix = `ann-snooze:${annId(a)}:v${annVersion(a)}:`
  for (const k of seen) if (k.startsWith(prefix)) return false
  return true
}

export type AnnouncementState = 'show' | 'snoozed' | 'off'
export function announcementState(a: AnnouncementRef, seen: Set<string>, day: string): AnnouncementState {
  if (seen.has(announcementOffKey(a))) return 'off'
  return seen.has(announcementSnoozeKey(a, day)) ? 'snoozed' : 'show'
}

/**
 * The pinned announcement this page load shows: the newest one neither switched off nor snoozed today. Each one
 * shown is snoozed for the day, so the next page load moves on to the next one; none is starved.
 */
export function pickAnnouncement<T extends AnnouncementRef>(pinnedNewestFirst: T[], seen: Set<string>, day: string): T | null {
  return pinnedNewestFirst.find(a => announcementState(a, seen, day) === 'show') ?? null
}
export const kimiPlanKey = (teamId: string, role: string) => `kimi:${teamId}:${role}`
export const quotaResetKey = (id: string) => `quota:${id}`
export const inboxKey = (rowId: string) => `inbox:${rowId}`
export const BROWSER_NOTICE_KEY = 'browser-notice:safari:v1'

export function parseSeenList(raw: string | null): string[] {
  try {
    const value = JSON.parse(raw ?? '[]')
    return Array.isArray(value) ? value.map(String).filter(Boolean) : []
  } catch { return [] }
}

/** The stored list after adding `keys`: most recent last, capped, no duplicates. */
export function rememberSeenList(list: string[], keys: string[]): string[] {
  const add = new Set(keys)
  return list.filter(k => !add.has(k)).concat([...add]).slice(-KEEP)
}
