// The organizers' "today's evaluation quota was reset" notice (pure, unit-tested). Logged-in contestants with a
// team see the latest reset of the last 24 hours once; dismissing remembers that reset's id, a later reset shows again.
export const QUOTA_RESET_SEEN_KEY = 'sac.quota-reset.seen'
const KEEP = 20
const DAY_MS = 24 * 60 * 60 * 1000

export interface QuotaResetPhase { phase_id: string; name_en: string; name_zh: string; daily_batches: number }
export interface QuotaResetNotice { id: string; reset_at: string; phases: QuotaResetPhase[] }

export function normalizeQuotaResetNotice(raw: unknown): QuotaResetNotice | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Record<string, unknown>
  if (typeof row.id !== 'string' || !row.id || typeof row.reset_at !== 'string') return null
  const phases = (Array.isArray(row.phases) ? row.phases : []).map(p => {
    const x = (p && typeof p === 'object' ? p : {}) as Record<string, unknown>
    return { phase_id: String(x.phase_id ?? ''), name_en: String(x.name_en ?? ''), name_zh: String(x.name_zh ?? x.name_en ?? ''),
      daily_batches: Number(x.daily_batches) }
  }).filter(p => Number.isFinite(p.daily_batches) && p.daily_batches > 0)
  if (!phases.length) return null
  return { id: row.id, reset_at: row.reset_at, phases }
}

export function parseQuotaResetSeen(raw: string | null): Set<string> {
  try {
    const value = JSON.parse(raw ?? '[]')
    return new Set(Array.isArray(value) ? value.map(String) : [])
  } catch { return new Set() }
}

/** The stored value after dismissing `id`: earlier ids plus this one, the most recent last, capped. */
export function rememberQuotaResetSeen(raw: string | null, id: string): string {
  return JSON.stringify([...parseQuotaResetSeen(raw)].filter(x => x !== id).concat(id).slice(-KEEP))
}

export function shouldShowQuotaReset(notice: QuotaResetNotice | null, seen: Set<string>, now = Date.now()): boolean {
  if (!notice || seen.has(notice.id)) return false
  const at = Date.parse(notice.reset_at)
  return Number.isFinite(at) && at <= now + 60_000 && now - at < DAY_MS
}

/** The notice text; the per-phase daily amount comes from the backend, never hard-coded. */
export function quotaResetText(notice: QuotaResetNotice): { en: string; zh: string } {
  const counts = [...new Set(notice.phases.map(p => p.daily_batches))]
  const cycle = { en: ' (the daily cycle starts at 08:00 Beijing time / 00:00 UTC).', zh: '（按北京时间 8 点 / UTC 0 点的每日周期计算）。' }
  if (counts.length === 1) {
    return { en: `Every team has ${counts[0]} evaluations again today${cycle.en} Earlier results and scores are unchanged.`,
      zh: `今天每队重新有 ${counts[0]} 次评测机会${cycle.zh}之前的评测记录和成绩不受影响。` }
  }
  return {
    en: `Every team has its full daily evaluations again today (${notice.phases.map(p => `${p.name_en}: ${p.daily_batches}`).join(', ')})${cycle.en} Earlier results and scores are unchanged.`,
    zh: `今天每队重新有完整的每日评测机会（${notice.phases.map(p => `${p.name_zh} ${p.daily_batches} 次`).join('、')}）${cycle.zh}之前的评测记录和成绩不受影响。`,
  }
}
