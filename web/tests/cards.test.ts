import assert from 'node:assert/strict'
import test from 'node:test'
import { githubUrl, normalizePersonCard, normalizeTeamCard } from '../src/lib/cards.ts'

test('person cards are normalized defensively and never invent fields', () => {
  assert.equal(normalizePersonCard(null), null)
  assert.equal(normalizePersonCard({ name: 'x' }), null)
  const c = normalizePersonCard({ id: 'u', name: 'Ann', blurb: '  ', seeking: 'ai', seeking_count: 0, astro_level: '2', wechat_qr: 'u/q.png', email: 'a@b' })!
  assert.equal(c.blurb, null)
  assert.equal(c.seeking_count, 1)
  assert.equal(c.astro_level, 2)
  assert.equal(c.wechat_qr, 'u/q.png')
  assert.ok(!('email' in c))
})

test('team cards keep only well-formed members', () => {
  const t = normalizeTeamCard({ id: 't', name: 'T', max_size: 3, members: [null, { name: 'no id' }, { id: 'm', name: 'M', is_leader: true }] })!
  assert.deepEqual(t.members.map(m => [m.id, m.is_leader]), [['m', true]])
  assert.equal(t.project_idea, null)
})

test('GitHub links only for plausible usernames', () => {
  assert.equal(githubUrl('https://github.com/octo/'), 'https://github.com/octo')
  assert.equal(githubUrl('@octo'), 'https://github.com/octo')
  assert.equal(githubUrl('not a handle!'), null)
  assert.equal(githubUrl(null), null)
})
