// The next public stage of the event (for example the formal competition while the
// Practice runs). Kept free of runtime imports so `npm test` can load it directly.

export interface ScheduledPhase {
  id: string; slug: string; name_en: string; name_zh: string; sort_order: number
  starts_at: string | null; ends_at: string | null; is_active: boolean
}
type Settings = { access_team_id?: string | null; sealed?: boolean } | null | undefined
export type ScheduledPhaseRow = ScheduledPhase & { observer_settings?: Settings | Settings[] }

/**
 * Active phases that start after `now`, soonest first. Row-level security already hides
 * team-restricted phases from everyone else; an administrator or the access team still
 * sees them, so they are dropped here too: a test phase is never the event's next stage.
 * The sealed hidden final phase is not a stage participants enter; it is dropped as well.
 */
export function upcomingPublicPhases(rows: ScheduledPhaseRow[], now: number): ScheduledPhase[] {
  return rows
    .filter(row => {
      const settings = Array.isArray(row.observer_settings) ? row.observer_settings[0] : row.observer_settings
      const start = row.starts_at ? Date.parse(row.starts_at) : NaN
      return row.is_active && start > now && !settings?.access_team_id && !settings?.sealed
    })
    .map(row => ({ id: row.id, slug: row.slug, name_en: row.name_en, name_zh: row.name_zh, sort_order: row.sort_order,
      starts_at: row.starts_at, ends_at: row.ends_at, is_active: row.is_active }))
    .sort((a, b) => Date.parse(a.starts_at!) - Date.parse(b.starts_at!) || a.sort_order - b.sort_order)
}

export type TimeLeft = { unit: 'days' | 'day' | 'hours' | 'soon'; n: number }
/** Whole days (or hours on the last day) until `startsAt`, for "9 days to go". */
export function timeLeft(startsAt: string, now: number): TimeLeft {
  const ms = Date.parse(startsAt) - now
  const days = Math.floor(ms / 86_400_000)
  if (days >= 2) return { unit: 'days', n: days }
  if (days === 1) return { unit: 'day', n: 1 }
  const hours = Math.floor(ms / 3_600_000)
  return hours >= 1 ? { unit: 'hours', n: hours } : { unit: 'soon', n: 0 }
}

/** Event dates are announced in Beijing time (the online competition: October 5–7, Beijing time). */
export const EVENT_TIME_ZONE = 'Asia/Shanghai'

const LOCALE_TAGS: Record<string, string> = { zh: 'zh-CN', en: 'en', ja: 'ja-JP', fr: 'fr-FR' }
/** The start day in `timeZone` (default: the viewer's own) ("10月5日", "October 5", "5 octobre"). */
export function startDay(startsAt: string, locale: string, timeZone?: string): string {
  return new Intl.DateTimeFormat(LOCALE_TAGS[locale] ?? locale, { month: 'long', day: 'numeric', timeZone }).format(new Date(startsAt))
}
/** The full local start time with its zone, for a tooltip. */
export function startMoment(startsAt: string, locale: string, timeZone?: string): string {
  return new Intl.DateTimeFormat(LOCALE_TAGS[locale] ?? locale, {
    year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short', timeZone,
  }).format(new Date(startsAt))
}
