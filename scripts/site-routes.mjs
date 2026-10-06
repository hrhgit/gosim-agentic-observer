// Static deep links of the platform app. GitHub Pages only knows files, so every
// static route gets its own copy of the finished app shell at <route>/index.html
// and answers 200 instead of the 404 fallback. Dynamic or unknown paths
// (/submissions/:id, typos) keep the 404 fallback plus restore-route.js.
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Route (relative to the app base) → page key in web/src/i18n/zh.json `meta.pages`,
 * the same key the router gives `applyDocumentMeta`, so the static title matches the
 * one the app sets after it starts. Redirect-only routes keep old links working.
 * `leaderboard/<slug>` pages cover the public boards linked from the site (phase slugs).
 */
export const STATIC_ROUTES = {
  start: 'start',
  brief: 'brief',
  vision: 'brief',
  rules: 'rules',
  cards: 'cards',
  'cards/alpha': 'cards',
  'cards/beta': 'cards',
  'cards/gamma': 'cards',
  'cards/delta': 'cards',
  'cards/a': 'cards',
  'cards/b': 'cards',
  'cards/c': 'cards',
  'cards/d': 'cards',
  docs: 'docs',
  cli: 'cli',
  faq: 'faq',
  about: 'about',
  resources: 'resources',
  leaderboard: 'leaderboard',
  'leaderboard/practice': 'leaderboard',
  'leaderboard/online': 'leaderboard',
  'leaderboard/practice-projects': 'leaderboard',
  announcements: 'announcements',
  teammates: 'teammates',
  register: 'register',
  login: 'register',
  forgot: 'register',
  reset: 'reset',
  dashboard: 'dashboard',
  team: 'team',
  notifications: 'team',
  compete: 'submit',
  submit: 'submit',
  projects: 'submit',
  participate: 'submit',
  history: 'submissions',
  records: 'submissions',
  submissions: 'submissions',
  profile: 'profile',
  admin: 'admin',
  'admin/phases': 'admin',
  'admin/scenarios': 'admin',
  'admin/submissions': 'admin',
  'admin/incidents': 'admin',
  'admin/users': 'admin',
  'admin/teams': 'admin',
  'admin/announcements': 'admin',
  'admin/credits': 'admin',
  'admin/settings': 'admin',
}

const PARAM = /^:\w+(\([^)]*\))?[?*+]?$/

/** Every path the router source can serve without a parameter value, e.g. `/rules` or `/leaderboard` (from `/leaderboard/:phase?`). */
export function routerStaticPaths(source) {
  const found = new Set()
  const add = path => {
    const segments = path.split('/').filter(Boolean)
    const firstParam = segments.findIndex(segment => segment.startsWith(':'))
    if (firstParam < 0) { if (segments.length) found.add(segments.join('/')); return }
    // A parameter the path can do without (`?` or `*`) leaves its static prefix routable.
    const optional = segments.slice(firstParam).every(segment => PARAM.test(segment) && /[?*]$/.test(segment))
    if (optional && firstParam > 0) found.add(segments.slice(0, firstParam).join('/'))
  }
  for (const match of source.matchAll(/\bpath\s*:\s*(['"`])(\/[^'"`]*)\1/g)) add(match[2])
  // Routes generated from a list, e.g. `...['/submit','/projects'].map(path => ({ path, redirect }))`.
  for (const match of source.matchAll(/\[([^\]]*)\]\s*\.map\(\s*\(?\s*path\b/g)) {
    for (const item of match[1].matchAll(/(['"`])(\/[^'"`]*)\1/g)) add(item[2])
  }
  return [...found].sort()
}

/** Router patterns with parameters, as matchers (`/leaderboard/:phase?` matches `leaderboard/online`). */
function dynamicMatchers(source) {
  const matchers = []
  for (const match of source.matchAll(/\bpath\s*:\s*(['"`])(\/[^'"`]*:[^'"`]*)\1/g)) {
    const segments = match[2].split('/').filter(Boolean)
    if (segments[0]?.startsWith(':')) continue // the catch-all 404 route matches everything
    let pattern = '^'
    for (const segment of segments) {
      if (!segment.startsWith(':')) pattern += '/' + segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      else pattern += /[?*]$/.test(segment) ? '(?:/[^/]+)?' : '/[^/]+'
    }
    matchers.push(new RegExp(pattern + '$'))
  }
  return matchers
}

/** Problems between the router and the route table: static routes without a page, pages for unknown routes, unknown page keys. */
export function routeCoverageProblems(routerSource, routes = STATIC_ROUTES, pages = null) {
  const statics = routerStaticPaths(routerSource)
  const matchers = dynamicMatchers(routerSource)
  const problems = []
  for (const path of statics) if (!(path in routes)) problems.push(`router path /${path} has no static page in scripts/site-routes.mjs`)
  for (const path of Object.keys(routes)) {
    if (!statics.includes(path) && !matchers.some(matcher => matcher.test('/' + path))) problems.push(`static page /${path} matches no router path`)
    if (pages && !pages[routes[path]]?.title) problems.push(`static page /${path} uses unknown page key ${routes[path]}`)
  }
  return problems
}

const escapeHtml = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** The app shell with a page-specific title (and og:title / descriptions when the shell has them). */
export function withPageMeta(html, { title, description }) {
  let out = html.replace(/<title>[\s\S]*?<\/title>/, () => `<title>${escapeHtml(title)}</title>`)
  const meta = (attribute, name, value) => {
    const tag = new RegExp(`(<meta\\s+${attribute}=["']${name}["']\\s+content=)(["'])[\\s\\S]*?\\2`)
    out = out.replace(tag, (_all, head) => `${head}"${escapeHtml(value)}"`)
  }
  meta('property', 'og:title', title)
  if (description) { meta('name', 'description', description); meta('property', 'og:description', description) }
  return out
}

/**
 * GitHub Pages answers /rules (a directory now) with a redirect to /rules/. Before Supabase or the app
 * read the address, drop that slash again and keep the query and any auth fragment untouched.
 */
export const CANONICAL_PATH_SCRIPT = "<script>(function(){var p=location.pathname;if(p.length>1&&p.slice(-1)==='/')"
  + "history.replaceState(null,'',p.replace(/\\/+$/,'')+location.search+location.hash)})()</script>"

/** Write <route>/index.html for every static route; returns the written paths relative to `directory`. */
export function writeRouteIndexes(directory, shell, messages, routes = STATIC_ROUTES) {
  const brand = messages?.meta?.brand
  const pages = messages?.meta?.pages ?? {}
  const written = []
  for (const [path, key] of Object.entries(routes)) {
    const page = pages[key]
    if (!page?.title || !brand) throw new Error(`No page title for /${path} (${key})`)
    const file = join(path, 'index.html')
    mkdirSync(join(directory, path), { recursive: true })
    const html = withPageMeta(shell, { title: `${page.title} · ${brand}`, description: page.description })
    writeFileSync(join(directory, file), html.replace(/<head>/, () => `<head>\n${CANONICAL_PATH_SCRIPT}`))
    written.push(file)
  }
  return written
}
