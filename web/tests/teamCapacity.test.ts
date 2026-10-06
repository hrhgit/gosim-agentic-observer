import assert from 'node:assert/strict'
import test from 'node:test'
import { parseTeamCapacity, showsTeamPlaces, teamCreationBlocked } from '../src/lib/teamCapacity.ts'

test('capacity is derived from the limit and the visible team count', () => {
  assert.deepEqual(parseTeamCapacity({ limit: 150, teams: 94, remaining: 56, full: false }), { limit: 150, teams: 94, remaining: 56, full: false })
  assert.deepEqual(parseTeamCapacity({ limit: 150, teams: 150 }), { limit: 150, teams: 150, remaining: 0, full: true })
  assert.deepEqual(parseTeamCapacity({ limit: 150, teams: 151 }), { limit: 150, teams: 151, remaining: 0, full: true })
})

test('a malformed payload is unknown, never full', () => {
  for (const bad of [null, undefined, 'full', [], {}, { limit: '150', teams: 3 }, { limit: 150, teams: -1 }, { limit: 1.5, teams: 0 }]) {
    assert.equal(parseTeamCapacity(bad), null)
  }
  assert.equal(teamCreationBlocked(null, false), false)
})

test('only non-admins are blocked when every place is taken', () => {
  const full = parseTeamCapacity({ limit: 2, teams: 2 })
  const open = parseTeamCapacity({ limit: 2, teams: 1 })
  assert.equal(teamCreationBlocked(full, false), true)
  assert.equal(teamCreationBlocked(full, undefined), true)
  assert.equal(teamCreationBlocked(full, true), false)
  assert.equal(teamCreationBlocked(open, false), false)
})

test('the places line shows for a real limit and hides when the limit means "no limit"', () => {
  assert.equal(showsTeamPlaces(parseTeamCapacity({ limit: 150, teams: 94 })), true)
  assert.equal(showsTeamPlaces(parseTeamCapacity({ limit: 9999, teams: 0 })), true)
  assert.equal(showsTeamPlaces(parseTeamCapacity({ limit: 100000, teams: 150 })), false)
  assert.equal(showsTeamPlaces(parseTeamCapacity({ limit: 10000, teams: 3 })), false)
  // A full real limit shows the "full" notice instead; unknown capacity shows nothing.
  assert.equal(showsTeamPlaces(parseTeamCapacity({ limit: 150, teams: 150 })), false)
  assert.equal(showsTeamPlaces(null), false)
})
