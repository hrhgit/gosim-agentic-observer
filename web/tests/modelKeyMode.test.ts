import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { DEFAULT_MODEL_KEY_MODE, DEFAULT_MODEL_PROTOCOL, relayMissesHiddenFinal, teamModelMode,
  teamModelProtocol } from '../src/lib/modelKeyMode.ts'

test('saving the key encrypted on the server is the default; an explicit relay choice is never overridden', () => {
  assert.equal(DEFAULT_MODEL_KEY_MODE, 'stored')
  assert.equal(teamModelMode(null), 'stored')
  assert.equal(teamModelMode(undefined), 'stored')
  assert.equal(teamModelMode({}), 'stored')
  assert.equal(teamModelMode({ mode: 'unknown' }), 'stored')
  assert.equal(teamModelMode({ mode: 'stored' }), 'stored')
  assert.equal(teamModelMode({ mode: 'relay' }), 'relay')
})

test('OpenAI-compatible is the default protocol; the platform never guesses Anthropic', () => {
  assert.equal(DEFAULT_MODEL_PROTOCOL, 'openai')
  assert.equal(teamModelProtocol(null), 'openai')
  assert.equal(teamModelProtocol(undefined), 'openai')
  assert.equal(teamModelProtocol({}), 'openai')
  assert.equal(teamModelProtocol({ protocol: 'openai' }), 'openai')
  assert.equal(teamModelProtocol({ protocol: 'unknown' }), 'openai')
  assert.equal(teamModelProtocol({ protocol: 'anthropic' }), 'anthropic')
})

test('every locale explains the trade-off and marks saving as the default', () => {
  const marks: Record<string, string> = { zh: '默认', en: 'default', fr: 'par défaut', ja: '既定' }
  for (const [lang, mark] of Object.entries(marks)) {
    const words = JSON.parse(readFileSync(new URL(`../src/i18n/${lang}.json`, import.meta.url), 'utf8')).submit.model_api
    for (const key of ['tradeoff', 'relay', 'relay_help', 'stored', 'stored_help']) assert.ok(words[key]?.trim(), `${lang}.${key}`)
    assert.ok(words.stored.includes(mark), `${lang}: the stored option is labelled as the default`)
    assert.ok(!words.relay.includes(mark), `${lang}: the relay option is not labelled as the default`)
  }
  // One plain sentence per language, naming both choices.
  const zh = JSON.parse(readFileSync(new URL('../src/i18n/zh.json', import.meta.url), 'utf8')).submit.model_api
  assert.equal(zh.tradeoff, '加密保存（默认）：成绩核实后自动删除，评测时无需保持页面打开。不保存：评测时需保持页面打开。')
})

test('relay teams are warned that the hidden final cannot use their key', () => {
  assert.equal(relayMissesHiddenFinal('relay', true), true)
  assert.equal(relayMissesHiddenFinal('stored', true), false)
  assert.equal(relayMissesHiddenFinal('relay', false), false)
  const view = readFileSync(new URL('../src/components/competition/ProjectWorkflow.vue', import.meta.url), 'utf8')
  assert.ok(view.includes('如果你的程序会调用大模型，请在比赛结束前把模型 API 改为『加密保存』，否则隐藏任务卡 E–H 评测时模型调用会失败。'))
  assert.ok(view.includes('data-testid="final-version-relay-warning"') && view.includes('data-testid="model-mode-final-note"'))
})
