import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import vm from 'node:vm'
import { CANONICAL_PATH_SCRIPT, STATIC_ROUTES, routeCoverageProblems, routerStaticPaths, withPageMeta, writeRouteIndexes } from './site-routes.mjs'

const router = readFileSync(new URL('../web/src/router.ts', import.meta.url), 'utf8')
const zh = JSON.parse(readFileSync(new URL('../web/src/i18n/zh.json', import.meta.url), 'utf8'))

test('every static route of the app router has a static page with a known title', () => {
  assert.deepEqual(routeCoverageProblems(router, STATIC_ROUTES, zh.meta.pages), [])
  for (const path of ['rules', 'leaderboard', 'vision', 'login', 'forgot', 'submit', 'projects', 'admin/settings']) {
    assert.ok(routerStaticPaths(router).includes(path), path)
  }
  // Parameterised pages stay on the 404 fallback; the public boards get their own page.
  assert.ok(!routerStaticPaths(router).some(path => path.includes(':')))
  for (const board of ['practice', 'practice-projects', 'online']) assert.ok(`leaderboard/${board}` in STATIC_ROUTES, board)
})

test('a static route added to the router without a page fails the check', () => {
  const grown = router.replace("{ path: '/faq',", "{ path: '/prizes', component: () => import('./pages/PrizesPage.vue'), meta: { page: 'prizes' }},\n    { path: '/faq',")
  assert.deepEqual(routeCoverageProblems(grown, STATIC_ROUTES), ['router path /prizes has no static page in scripts/site-routes.mjs'])
  const listed = router.replace("['/submit','/projects','/participate']", "['/submit','/projects','/participate','/upload']")
  assert.deepEqual(routeCoverageProblems(listed, STATIC_ROUTES), ['router path /upload has no static page in scripts/site-routes.mjs'])
  const removed = router.replace("{ path: '/faq', component: () => import('./pages/FaqPage.vue') , meta: { page: 'faq' }},", '')
  assert.notEqual(removed, router)
  assert.deepEqual(routeCoverageProblems(removed, STATIC_ROUTES), ['static page /faq matches no router path'])
})

test('optional parameters keep their static prefix and required ones are skipped', () => {
  const source = "routes: [{ path: '/a/:x?' }, { path: '/b/:id' }, { path: '/c/d' }, { path: '/:rest(.*)*' }, ...['/e','/f'].map(path => ({ path }))]"
  assert.deepEqual(routerStaticPaths(source), ['a', 'c/d', 'e', 'f'])
})

test('route pages are copies of the finished shell with their own title', () => {
  const shell = '<!doctype html><html><head>\n<script src="/survey26/platform/restore-route.js"></script>\n'
    + '<title>智能体巡天黑客松 · Agentic Observer</title>\n<meta name="description" content="generic" />\n'
    + '<meta property="og:title" content="old" />\n<script type="module" crossorigin src="/survey26/platform/assets/index-abc.js"></script>\n'
    + '</head><body><div id="app"></div></body></html>'
  const directory = mkdtempSync(join(tmpdir(), 'route-pages-'))
  try {
    const written = writeRouteIndexes(directory, shell, zh)
    assert.equal(written.length, Object.keys(STATIC_ROUTES).length)
    assert.ok(written.includes('rules/index.html') && written.includes('leaderboard/online/index.html') && written.includes('admin/phases/index.html'))
    const rules = readFileSync(join(directory, 'rules/index.html'), 'utf8')
    assert.match(rules, /<title>规则 · 智能体巡天黑客松<\/title>/)
    assert.match(rules, /<meta property="og:title" content="规则 · 智能体巡天黑客松" \/>/)
    assert.match(rules, new RegExp(`<meta name="description" content="${zh.meta.pages.rules.description}" />`))
    assert.ok(rules.startsWith(`<!doctype html><html><head>\n${CANONICAL_PATH_SCRIPT}\n<script src="/survey26/platform/restore-route.js">`))
    const bare = html => html.replace(CANONICAL_PATH_SCRIPT + '\n', '').replace(/<title>.*<\/title>|<meta [^>]*>/g, '')
    assert.equal(bare(rules), bare(shell))
    assert.match(readFileSync(join(directory, 'vision/index.html'), 'utf8'), /<title>完整赛事说明 · 智能体巡天黑客松<\/title>/)
    assert.match(readFileSync(join(directory, 'login/index.html'), 'utf8'), /<title>报名 · 智能体巡天黑客松<\/title>/)
    assert.match(readFileSync(join(directory, 'leaderboard/practice/index.html'), 'utf8'), /<title>排行榜 · 智能体巡天黑客松<\/title>/)
  } finally { rmSync(directory, { recursive: true, force: true }) }
})

test('titles are escaped and shells without social tags are left alone', () => {
  const html = withPageMeta('<head><title>x</title></head>', { title: 'A & <B>', description: 'd' })
  assert.equal(html, '<head><title>A &amp; &lt;B&gt;</title></head>')
  assert.throws(() => writeRouteIndexes(tmpdir(), '<title>x</title>', { meta: { brand: 'b', pages: {} } }, { rules: 'rules' }), /No page title/)
})

test('the Pages trailing-slash redirect is undone before the app reads the address', () => {
  const script = CANONICAL_PATH_SCRIPT.replace(/^<script>|<\/script>$/g, '')
  const visit = (pathname, search = '', hash = '') => {
    let replaced = null
    vm.runInNewContext(script, { location: { pathname, search, hash }, history: { replaceState: (_state, _title, url) => { replaced = url } } })
    return replaced
  }
  assert.equal(visit('/survey26/platform/reset/', '', '#access_token=t&type=recovery'), '/survey26/platform/reset#access_token=t&type=recovery')
  assert.equal(visit('/survey26/platform/team/', '?invite=ABCD1234'), '/survey26/platform/team?invite=ABCD1234')
  assert.equal(visit('/survey26/platform/leaderboard/online/'), '/survey26/platform/leaderboard/online')
  assert.equal(visit('/survey26/platform/rules'), null)
  assert.equal(visit('/'), null)
})
