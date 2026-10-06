const pad = (n: number) => String(n).padStart(2, '0')

/** ISO timestamp → "YYYY-MM-DD HH:MM" in UTC ("—" when empty). */
export function fmtUtc(value: string | null | undefined, opts: { seconds?: boolean; short?: boolean } = {}): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  const date = opts.short ? `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` : `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
  const time = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${opts.seconds ? `:${pad(d.getUTCSeconds())}` : ''}`
  return `${date} ${time}`
}
/** ISO timestamp → value for <input type="datetime-local"> expressed in UTC. */
export function toLocalInput(value: string | null | undefined): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}
/** datetime-local value (interpreted as UTC) → ISO string or null. */
export function fromLocalInput(value: string): string | null {
  if (!value) return null
  const d = new Date(`${value}${value.length === 16 ? ':00' : ''}Z`)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}
export function num(value: unknown, digits = 2): string {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return '—'
  return Number(value).toFixed(digits)
}
export function pct(value: unknown, digits = 1): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return `${(Number(value) * 100).toFixed(digits)}%`
}
export function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** A GitHub username from a profile field ("@name", "name" or a github.com link), or ''
 * when it cannot be one (e.g. a Chinese name or a phrase with spaces), so no request is
 * made to github.com for a handle that does not exist. */
export function githubHandle(value: string | null | undefined): string {
  const handle = String(value ?? '').trim().replace(/^@/, '')
    .replace(/^https?:\/\/(www\.)?github\.com\//i, '').replace(/\/.*$/, '')
  return /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(handle) ? handle : ''
}

export function teamAvatar(name: string): { initial: string; hue: number } {
  const trimmed = (name || '?').trim()
  let hash = 0
  for (const ch of trimmed) hash = (hash * 31 + ch.codePointAt(0)!) >>> 0
  return { initial: [...trimmed][0]!.toUpperCase(), hue: hash % 360 }
}
