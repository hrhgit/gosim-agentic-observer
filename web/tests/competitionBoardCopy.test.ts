import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

// Competition numbers as configured for the phases (scripts/configure-v4-phases.py) and stated on the Rules page:
// online cards A–D, 900 s per card, 40 evaluations per team per day; final ranking = mean over the hidden cards E–H.
const load = (locale: string) => JSON.parse(readFileSync(new URL(`../src/i18n/${locale}.json`, import.meta.url), 'utf8'))
const get = (o: any, path: string) => path.split('.').reduce((v, k) => v?.[k], o)
const KEYS = ['leaderboard.public_board', 'leaderboard.final_board', 'leaderboard.mean_note', 'leaderboard.detail.board_mean',
  'home.leaderboard.lede', 'home.submission.items.1.desc', 'faq.items.5.a', 'faq.items.7.a', 'faq.items.8.a']

test('competition copy describes cards A-D, 900 s, 40 evaluations a day and the hidden cards E-H', () => {
  for (const [locale, perDay] of [['en', /40 evaluations per team per day/], ['zh', /每队每天 40 次评测/]] as const) {
    const m = load(locale)
    const text = KEYS.map(k => String(get(m, k))).join('\n')
    assert.match(get(m, 'leaderboard.public_board'), /A–D/)
    assert.match(get(m, 'leaderboard.public_board'), /900/)
    assert.match(get(m, 'leaderboard.final_board'), /E–H/)
    assert.match(get(m, 'faq.items.7.a'), perDay)
    assert.match(get(m, 'faq.items.5.a'), /900/)
    assert.doesNotMatch(text, /three|三个|3600 s per scenario|每个场景 3600|10 (complete )?batches|10 个完整批次|hidden scenario|隐藏(决赛)?场景/)
  }
})

const src = (path: string) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
const STALE = /A ?[–-] ?C\b|three (formal )?(scenarios|cards)|三个(正式)?场景|三张|hidden scenario|隐藏场景|一张隐藏|每(个场景|张卡) 3600|3600 s per|16、25、9、100|16, 25, 9 and 100|最多 16 根|up to 16 targets|最多命中 16 个目标|at most 16 targets/

test('rules, docs, task cards and the final-version panel agree on A-D, E-H and per-card fibres', () => {
  const files = ['components/competition/ProjectWorkflow.vue',
    ...['zh', 'en'].flatMap(l => [`content/rules.${l}.md`, `content/docs.${l}.md`,
      ...['template', 'alpha', 'beta', 'gamma', 'delta'].map(c => `content/taskcard.${c}.v4.${l}.md`)])]
  for (const file of files) {
    const match = src(file).match(STALE)
    assert.equal(match, null, `${file}: ${match?.[0]}`)
  }
  assert.match(src('components/competition/ProjectWorkflow.vue'), /隐藏任务卡 E–H/)
  for (const l of ['zh', 'en']) assert.match(src(`content/docs.${l}.md`), /`n_fibers`/)
})

test('the FAQ explains how dependencies are installed', () => {
  for (const locale of ['zh', 'en']) {
    const a = load(locale).faq.items.map((i: any) => i.a).join('\n')
    assert.match(a, /requirements\.txt/)
    assert.match(a, /observer\.project\.json/)
    assert.match(a, /--target/)
  }
})

test('the FAQ and the rules use the current protocol and data-release terms', () => {
  for (const locale of ['zh', 'en']) {
    const faq = load(locale).faq.items.map((i: any) => `${i.q}\n${i.a}`).join('\n')
    assert.doesNotMatch(faq, /tile_science_value|candidate tiles|候选天区|weather\.csv|weather_events\.csv|agent_initialization_error|场景|scenario/i)
    assert.match(faq, /agent_error/)
    const rules = src(`content/rules.${locale}.md`)
    assert.doesNotMatch(rules, /含天气、预报与事件文件|including the weather, forecast and event files/)
  }
})
