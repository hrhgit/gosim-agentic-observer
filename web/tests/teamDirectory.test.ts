import assert from 'node:assert/strict'
import test from 'node:test'
import { directoryCounts, filterTeams, isRecruiting, type DirectoryTeam } from '../src/lib/teamDirectory.ts'

const team = (id: string, name: string, member_count: number, max_size: number, is_locked = false): DirectoryTeam =>
  ({ id, name, member_count, max_size, is_locked })
const teams = [
  team('a', 'Pleiades', 1, 3),
  team('b', 'STAR SEEING', 2, 3),
  team('c', '北十字', 3, 3),
  team('d', 'Solo Star', 1, 1),
  team('e', 'Paused stars', 1, 3, true),
  team('f', 'acceptance-w04-验收测试队-30f6-with-a-very-long-name', 1, 3),
]

test('recruiting means open and below the size limit', () => {
  assert.deepEqual(teams.filter(isRecruiting).map(t => t.id), ['a', 'b', 'f'])
  // team_directory() returns member_count as bigint; strings from JSON still compare numerically.
  assert.equal(isRecruiting({ ...team('x', 'x', 0, 3), member_count: '2' as unknown as number }), true)
  assert.equal(isRecruiting({ ...team('x', 'x', 0, 3), member_count: '10' as unknown as number, max_size: 3 }), false)
})

test('the default view lists recruiting teams only, in the directory order', () => {
  assert.deepEqual(filterTeams(teams, '', false).map(t => t.id), ['a', 'b', 'f'])
  assert.deepEqual(filterTeams(teams, '', true).map(t => t.id), ['a', 'b', 'c', 'd', 'e', 'f'])
  assert.deepEqual(directoryCounts(teams), { all: 6, recruiting: 3 })
})

test('search matches names case-insensitively, ignoring surrounding spaces', () => {
  assert.deepEqual(filterTeams(teams, 'star', false).map(t => t.id), ['b'])
  assert.deepEqual(filterTeams(teams, '  STAR ', true).map(t => t.id), ['b', 'd', 'e'])
  assert.deepEqual(filterTeams(teams, '十字', true).map(t => t.id), ['c'])
  assert.deepEqual(filterTeams(teams, '十字', false), [])
  assert.deepEqual(filterTeams(teams, 'VERY-LONG', false).map(t => t.id), ['f'])
  assert.deepEqual(filterTeams([], 'x', true), [])
})
