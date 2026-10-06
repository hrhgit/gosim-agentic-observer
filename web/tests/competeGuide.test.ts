import assert from 'node:assert/strict'
import test from 'node:test'
import { COMPETE_GUIDE_KEY, GUIDE_STEPS, clipRect, guideLang, placeTooltip, resolveStep, shouldAutoShow, stepCopy } from '../src/lib/competeGuide.ts'
import { pickPopup, rememberSeenList } from '../src/lib/popupRules.ts'

const step = (id: string) => GUIDE_STEPS.find(s => s.id === id)!

test('the tour covers the workspace in order, every step in zh and en', () => {
  assert.deepEqual(GUIDE_STEPS.map(s => s.id), ['entry', 'upload', 'confirm', 'evaluate', 'results', 'settings', 'final'])
  for (const s of GUIDE_STEPS) {
    assert.ok(s.targets.length > 0)
    for (const lang of ['zh', 'en'] as const) assert.ok(s[lang].title && s[lang].body, `${s.id} ${lang}`)
  }
})

test('a step uses its first target on the page and is skipped when none is there', () => {
  const upload = step('upload')
  assert.equal(resolveStep(upload, () => false), null)
  const all = resolveStep(upload, () => true)!
  assert.equal(all.selector, upload.targets[0]); assert.equal(all.fallback, false)
  const form = resolveStep(upload, s => s === upload.targets[1])!
  assert.equal(form.selector, upload.targets[1]); assert.equal(form.fallback, true)
  // The practice/online switch only exists during the competition.
  assert.equal(resolveStep(step('entry'), s => !s.includes('entry-switch')), null)
  // Skipping keeps the order of the remaining steps.
  const onPage = new Set(GUIDE_STEPS.filter(s => s.id !== 'confirm' && s.id !== 'entry').map(s => s.targets.at(-1)!))
  const resolved = GUIDE_STEPS.map(s => resolveStep(s, sel => onPage.has(sel))).filter(Boolean).map(r => r!.step.id)
  assert.deepEqual(resolved, ['upload', 'evaluate', 'results', 'settings', 'final'])
})

test('during practice the final-version step explains it comes with the online phase', () => {
  const final = step('final')
  const present = resolveStep(final, () => true)!
  const practice = resolveStep(final, s => s.includes('compete-tab-settings'))!
  assert.match(stepCopy(present, 'zh').body, /截止前/)
  assert.match(stepCopy(practice, 'zh').body, /线上赛开始后/)
  assert.match(stepCopy(practice, 'en').body, /online phase starts/)
  // A step without fallback copy keeps its own text on a fallback target.
  const tab = resolveStep(step('results'), s => s.includes('compete-tab-history'))!
  assert.equal(stepCopy(tab, 'en').title, step('results').en.title)
})

test('ja and fr fall back to English', () => {
  assert.equal(guideLang('zh'), 'zh')
  for (const l of ['en', 'ja', 'fr'] as const) assert.equal(guideLang(l), 'en')
  const r = resolveStep(step('upload'), () => true)!
  assert.equal(stepCopy(r, 'fr').title, step('upload').en.title)
})

test('shown once per person, only on the simplified layout, and it never outranks other popups', () => {
  assert.equal(shouldAutoShow('v2', new Set()), true)
  assert.equal(shouldAutoShow('classic', new Set()), false)
  const seen = new Set(rememberSeenList([], [COMPETE_GUIDE_KEY]))
  assert.equal(shouldAutoShow('v2', seen), false)
  assert.equal(pickPopup(['compete-guide', 'kimi-plan']), 'kimi-plan')
  assert.equal(pickPopup(['compete-guide', 'something-new']), 'compete-guide')
  assert.equal(pickPopup(['compete-guide']), 'compete-guide')
})

test('the tooltip goes below the target, else above, else over it, and stays on a 390 px screen', () => {
  const phone = { width: 390, height: 844 }, tip = { width: 366, height: 170 }
  const below = placeTooltip({ top: 100, left: 20, width: 120, height: 40 }, tip, phone)
  assert.equal(below.side, 'below'); assert.equal(below.top, 152); assert.equal(below.left, 12)
  const above = placeTooltip({ top: 700, left: 300, width: 80, height: 40 }, tip, phone)
  assert.equal(above.side, 'above'); assert.equal(above.top, 700 - 12 - 170)
  assert.ok(above.left + tip.width <= phone.width - 12)
  const over = placeTooltip({ top: 0, left: 0, width: 390, height: 844 }, tip, phone)
  assert.equal(over.side, 'over'); assert.equal(over.top, 844 - 170 - 12)
  const wide = placeTooltip({ top: 100, left: 600, width: 200, height: 40 }, { width: 352, height: 170 }, { width: 1440, height: 900 })
  assert.equal(wide.left, 700 - 176)
})

test('a tall target is clipped to the screen', () => {
  assert.deepEqual(clipRect({ top: -200, left: 10, width: 300, height: 2000 }, { width: 390, height: 844 }), { top: 0, left: 4, width: 312, height: 844 })
  assert.deepEqual(clipRect({ top: 100, left: 10, width: 100, height: 50 }, { width: 390, height: 844 }), { top: 94, left: 4, width: 112, height: 62 })
})
