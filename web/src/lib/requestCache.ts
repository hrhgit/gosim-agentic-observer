// Shares concurrent calls and caches results for a short TTL, so SPA remounts and multiple
// callers in the same tick don't multiply backend requests (observer boards, leaderboard, the
// scenarios bucket file listing). Memory-backed by default; pass a Storage for persistence
// across full page reloads within the same tab (e.g. sessionStorage).
interface Entry<T> { value: T; expires: number }

const memory = new Map<string, Entry<unknown>>()
const inFlight = new Map<string, Promise<unknown>>()

function readEntry<T>(key: string, store?: Storage): Entry<T> | null {
  if (!store) return (memory.get(key) as Entry<T> | undefined) ?? null
  try {
    const raw = store.getItem(key)
    return raw ? (JSON.parse(raw) as Entry<T>) : null
  } catch { return null }
}

function writeEntry<T>(key: string, entry: Entry<T>, store?: Storage) {
  if (!store) { memory.set(key, entry); return }
  try { store.setItem(key, JSON.stringify(entry)) } catch { /* quota or unavailable: skip persisting */ }
}

/** Drop every cached entry whose key starts with `prefix`, so the next read is a fresh fetch. */
export function invalidatePrefix(prefix: string, store?: Storage) {
  if (!store) {
    for (const key of memory.keys()) if (key.startsWith(prefix)) memory.delete(key)
    return
  }
  try {
    for (const key of Object.keys(store)) if (key.startsWith(prefix)) store.removeItem(key)
  } catch { /* unavailable: nothing to clear */ }
}

export async function cached<T>(key: string, ttlMs: number, fn: () => Promise<T>, store?: Storage): Promise<T> {
  const hit = readEntry<T>(key, store)
  if (hit && hit.expires > Date.now()) return hit.value
  const pending = inFlight.get(key) as Promise<T> | undefined
  if (pending) return pending
  const promise = fn()
    .then(value => { writeEntry(key, { value, expires: Date.now() + ttlMs }, store); return value })
    .finally(() => { inFlight.delete(key) })
  inFlight.set(key, promise)
  return promise
}
