/**
 * Notice a stale tab and get it onto the current build.
 *
 * GitHub Pages serves index.html with `cache-control: max-age=600`, and Safari holds onto it far
 * longer than that — through the back/forward cache and across restores. Because the chunk names
 * are hashed and the old chunks stay on the CDN, a stale index.html keeps running an old build
 * quite happily, with no error to notice. So: re-fetch index.html past the cache, compare the entry
 * chunk it names with the one actually running, and act when they differ.
 *
 * A differing entry filename alone is not enough to call a tab stale: right after a deploy, the
 * CDN's edges take a little while to converge, so this re-fetch can just as easily land on an
 * edge still serving the *previous* build as on the new one — even when the tab already has the
 * latest. Each build is stamped with its own build time (vite.config.ts), so the fetched page is
 * only treated as an update when its stamp is strictly newer than the one already running; an
 * edge serving an older or identical generation is silently ignored instead of flagged.
 *
 * Only the very first check — on the initial page load, before the visitor has done anything —
 * is allowed to reload on its own, escalating because a plain reload is not always enough on
 * Safari: 1st time refreshes the cached index.html then reloads; 2nd time reloads through a
 * one-off query string the cache has never seen; after that it stops, so a misconfigured host can
 * never trap a visitor in a reload loop. Every later check (tab resumed from the back/forward
 * cache, tab brought back to the foreground, the slow heartbeat) just raises the UpdateBanner —
 * the visitor may be mid-form or mid-upload, so nothing here ever reloads out from under them.
 */
import { markUpdateAvailable } from './updateNotice'

const TRY_KEY = 'sac-fresh-attempt'
const ENTRY_RE = /assets\/index-[A-Za-z0-9_-]+\.js/
const BUILD_TIME_RE = /<meta name="app-build-time" content="(\d+)"/

export const isSafari = (): boolean => {
  if (typeof navigator === 'undefined') return false
  const ua = navigator.userAgent
  // Every iOS browser is WebKit, but only Safari itself lacks a vendor tag.
  return /safari/i.test(ua) && !/chrome|chromium|crios|android|fxios|edgios|edg\//i.test(ua)
}

function runningEntry(): string | null {
  const tag = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/index-"]')
  return tag?.src.match(ENTRY_RE)?.[0] ?? null
}

/** 0 for a build from before this stamp existed — always treated as older than any real stamp. */
function runningBuildTime(): number {
  const tag = document.querySelector<HTMLMetaElement>('meta[name="app-build-time"]')
  return Number(tag?.content) || 0
}

function attempts(): number {
  try { return Number(sessionStorage.getItem(TRY_KEY) || '0') } catch { return 99 }
}
function noteAttempt(n: number) {
  try { sessionStorage.setItem(TRY_KEY, String(n)) } catch { /* private mode */ }
}

interface Published { entry: string | null; buildTime: number }

async function fetchPublished(): Promise<Published | null> {
  const base = import.meta.env.BASE_URL || '/'
  const res = await fetch(`${base}index.html?fresh=${Date.now()}`, { cache: 'reload', credentials: 'omit' })
  if (!res.ok) return null
  const html = await res.text()
  return { entry: html.match(ENTRY_RE)?.[0] ?? null, buildTime: Number(html.match(BUILD_TIME_RE)?.[1]) || 0 }
}

async function checkOnce(initial: boolean) {
  const running = runningEntry()
  if (!running) return  // dev server, or a build without a hashed entry: nothing to compare
  let published: Published | null = null
  try { published = await fetchPublished() } catch { return }  // offline: leave the tab alone
  if (!published || !published.entry || published.entry === running) {
    noteAttempt(0)
    return
  }
  // Different filename, but not demonstrably newer (a lagging CDN edge, or the same build
  // restamped) — nothing to do. A genuinely newer build will keep re-triggering this on every
  // later check until it does carry a newer stamp.
  if (published.buildTime <= runningBuildTime()) return
  if (!initial) {
    markUpdateAvailable()
    return
  }
  const n = attempts() + 1
  noteAttempt(n)
  if (n === 1) {
    location.reload()
  } else if (n === 2) {
    const url = new URL(location.href)
    url.searchParams.set('_fresh', String(Date.now()))
    location.replace(url.toString())
  }
}

export function installFreshnessCheck() {
  if (typeof window === 'undefined') return
  // On load, whenever the tab comes back, and on a back/forward-cache restore (Safari's favourite
  // way of resurrecting an old page), plus a slow heartbeat for tabs left open for hours.
  window.addEventListener('load', () => window.setTimeout(() => void checkOnce(true), 1500))
  window.addEventListener('pageshow', event => { if ((event as PageTransitionEvent).persisted) void checkOnce(false) })
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void checkOnce(false) })
  window.setInterval(() => { if (!document.hidden) void checkOnce(false) }, 5 * 60 * 1000)
}
