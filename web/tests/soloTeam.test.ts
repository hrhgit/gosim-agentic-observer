import assert from 'node:assert/strict'
import test from 'node:test'
import { createSoloTeam, isNameTaken, SOLO_ATTEMPTS, soloTeamBaseName, soloTeamName } from '../src/lib/soloTeam.ts'

const taken = () => Object.assign(new Error('name_taken'), { code: 'P0001' })

test('the team is named after the nickname, then the name, then the e-mail local part', () => {
  assert.equal(soloTeamBaseName({ nickname: '  星际  漫游者 ', name: 'Li Hua', email: 'li@example.com' }), '星际 漫游者')
  assert.equal(soloTeamBaseName({ nickname: '', name: 'Li Hua', email: 'li@example.com' }), 'Li Hua')
  assert.equal(soloTeamBaseName({ nickname: null, name: '   ', email: 'ada.lovelace@example.com' }), 'ada.lovelace')
  // create_team needs two characters: a one-character nickname gives way to a longer source...
  assert.equal(soloTeamBaseName({ nickname: '李', name: 'Li Hua', email: 'li@example.com' }), 'Li Hua')
  // ...and when nothing is long enough the first source is kept with a suffix.
  assert.equal(soloTeamBaseName({ nickname: '李', name: '', email: 'l@example.com' }), '李-solo')
  assert.equal(soloTeamBaseName({}), 'solo')
  assert.equal([...soloTeamBaseName({ nickname: '星'.repeat(80) })].length, 60)
})

test('retry names keep a short suffix within the 60 character limit', () => {
  assert.equal(soloTeamName('Nova', 0, 'x1y2'), 'Nova')
  assert.equal(soloTeamName('Nova', 1, 'x1y2'), 'Nova-x1y2')
  const long = '观'.repeat(60)
  assert.equal([...soloTeamName(long, 2, 'ab12')].length, 60)
  assert.ok(soloTeamName(long, 2, 'ab12').endsWith('-ab12'))
})

test('only name collisions are retried', () => {
  assert.equal(isNameTaken(taken()), true)
  assert.equal(isNameTaken({ code: '23505', message: 'duplicate key value violates unique constraint "teams_name_key"' }), true)
  assert.equal(isNameTaken(new Error('already_in_team')), false)
  assert.equal(isNameTaken(null), false)
})

test('a free name is used as is', async () => {
  const tried: string[] = []
  const name = await createSoloTeam({ nickname: 'Nova' }, async n => { tried.push(n) }, () => 'zzzz')
  assert.equal(name, 'Nova')
  assert.deepEqual(tried, ['Nova'])
})

test('a taken name is retried with a random suffix', async () => {
  const tried: string[] = []
  const suffixes = ['k3p9', 'q7w2']
  const name = await createSoloTeam({ nickname: 'Nova' }, async n => { tried.push(n); if (tried.length < 3) throw taken() }, () => suffixes.shift()!)
  assert.equal(name, 'Nova-q7w2')
  assert.deepEqual(tried, ['Nova', 'Nova-k3p9', 'Nova-q7w2'])
})

test('it gives up after three collisions and never retries other errors', async () => {
  const tried: string[] = []
  await assert.rejects(createSoloTeam({ nickname: 'Nova' }, async n => { tried.push(n); throw taken() }, () => 'abcd'), /name_taken/)
  assert.equal(tried.length, SOLO_ATTEMPTS)
  assert.equal(SOLO_ATTEMPTS, 3)
  const once: string[] = []
  await assert.rejects(createSoloTeam({ nickname: 'Nova' }, async n => { once.push(n); throw new Error('already_in_team') }, () => 'abcd'), /already_in_team/)
  assert.deepEqual(once, ['Nova'])
})
