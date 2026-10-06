import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const LOCALES = ['zh', 'en', 'ja', 'fr'] as const
type Messages = Record<string, any>
const messages: Record<string, Messages> = Object.fromEntries(LOCALES.map(locale => [
  locale,
  JSON.parse(readFileSync(new URL(`../src/i18n/${locale}.json`, import.meta.url), 'utf8')),
]))

// The three-step walkthrough keeps the two stages' submission instructions separate.
const COMPETITION_WORDS = /正式赛|正式比赛|线上比赛|online competition|finals-preview|competition scenarios|正式大会|オンライン大会|compétition en ligne/i
const PRACTICE_WORDS = /练习赛|练习场景|Playground|\bpractice\b|練習|entraînement/i

function* flatten(value: unknown): Generator<string> {
  if (typeof value === 'string') yield value
  else if (Array.isArray(value)) for (const item of value) yield* flatten(item)
  else if (value && typeof value === 'object') for (const item of Object.values(value)) yield* flatten(item)
}

const STEP_KEYS = ['subtitle', 's1_title', 's1_desc', 's1_cta', 's2_title', 's2_lead', 's2_desc',
  's2_link', 's2_examples_link', 's3_title', 's3_intro', 's3_points', 's3_cta', 'help_prefix', 'help_faq', 'help_or', 'help_rules', 'help_suffix']

test('every locale carries the full three-step copy for both stages', () => {
  for (const locale of LOCALES) {
    for (const mode of ['practice', 'competition']) {
      const copy = messages[locale]!.start3?.[mode]
      assert.ok(copy, `${locale} start3.${mode} missing`)
      for (const key of STEP_KEYS) assert.ok(copy[key], `${locale} start3.${mode}.${key} missing`)
      assert.ok(copy.s3_points.length >= 1, `${locale} ${mode} needs the submission options`)
    }
  }
})

test('the practice copy never names the competition stage and vice versa', () => {
  for (const locale of LOCALES) {
    for (const text of flatten(messages[locale]!.start3.practice)) {
      assert.ok(!COMPETITION_WORDS.test(text), `${locale} practice copy: ${COMPETITION_WORDS.exec(text)?.[0]} in ${text}`)
    }
    for (const text of flatten(messages[locale]!.start3.competition)) {
      assert.ok(!PRACTICE_WORDS.test(text), `${locale} competition copy: ${PRACTICE_WORDS.exec(text)?.[0]} in ${text}`)
    }
    const header = [messages[locale]!.nav.start, messages[locale]!.nav.more, messages[locale]!.hero.cta_start]
    for (const text of header) {
      assert.ok(!COMPETITION_WORDS.test(text) && !PRACTICE_WORDS.test(text), `${locale} header label: ${text}`)
    }
  }
})

test('the walkthrough uses current project guidance and preserves the submission limits', () => {
  for (const locale of LOCALES) {
    // The starter-kit download was retired; new participants use the docs and example projects.
    assert.doesNotMatch([...flatten(messages[locale]!.start3)].join(' '), /local_runner\.py|agent-observer-starter-kit/)
  }
  assert.match(messages.zh!.start3.practice.s3_points.join(' '), /40 次/)
  assert.match(messages.zh!.start3.competition.s3_points.join(' '), /每队每天 40 次评测/)
  assert.match(messages.zh!.start3.competition.s3_points.join(' '), /A–D.*900 秒/)
  assert.match(messages.zh!.start3.competition.s3_points.join(' '), /隐藏任务卡 E–H/)
  assert.match(messages.en!.start3.practice.s3_points.join(' '), /40 per team per day/)
  assert.match(messages.en!.start3.competition.s3_points.join(' '), /40 evaluations per team per day/)
  assert.match(messages.en!.start3.competition.s3_points.join(' '), /A–D, 900 s per card/)
  assert.match(messages.en!.start3.competition.s3_points.join(' '), /hidden cards E–H/)
  for (const locale of LOCALES) assert.doesNotMatch(messages[locale]!.start3.competition.s3_points.join(' '), /three|三个|3 つ|trois|10 (batches|批|バッチ|lots)/)
})
