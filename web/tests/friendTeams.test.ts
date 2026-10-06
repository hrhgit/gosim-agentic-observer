import assert from 'node:assert/strict'
import test from 'node:test'
import { boardPhase, emptyContext, normalizeFriendTeams, rankFor, ranksByTeam, teamAction, type FriendTeam } from '../src/lib/friendTeams.ts'

const ft = (o: Partial<FriendTeam> = {}): FriendTeam => ({ user_id: 'u', team_id: 't', team_name: 'T', is_locked: false, max_size: 3, member_count: 1, requested: false, ...o })

test('captain invites a teamless friend; nobody else does', () => {
  assert.deepEqual(teamAction({ myTeam: true, captain: true, friendInTeam: false, friendTeam: undefined }), { kind: 'invite' })
  assert.equal(teamAction({ myTeam: true, captain: true, friendInTeam: true, friendTeam: ft() }), null)
  assert.equal(teamAction({ myTeam: true, captain: false, friendInTeam: false, friendTeam: undefined }), null)
})

test('teamless me asks to join a joinable team; otherwise disabled with a reason', () => {
  assert.deepEqual(teamAction({ myTeam: false, captain: false, friendInTeam: true, friendTeam: ft() }), { kind: 'apply', team_id: 't' })
  assert.deepEqual(teamAction({ myTeam: false, captain: false, friendInTeam: true, friendTeam: ft({ is_locked: true }) }), { kind: 'apply_disabled', reason: 'locked' })
  assert.deepEqual(teamAction({ myTeam: false, captain: false, friendInTeam: true, friendTeam: ft({ member_count: 3 }) }), { kind: 'apply_disabled', reason: 'full' })
  assert.deepEqual(teamAction({ myTeam: false, captain: false, friendInTeam: true, friendTeam: ft({ requested: true }) }), { kind: 'apply_disabled', reason: 'requested' })
  assert.deepEqual(teamAction({ myTeam: false, captain: false, friendInTeam: true, friendTeam: ft(), requested: true }), { kind: 'apply_disabled', reason: 'requested' })
  // Hidden team (not returned) or teamless friend: nothing.
  assert.equal(teamAction({ myTeam: false, captain: false, friendInTeam: true, friendTeam: undefined }), null)
})

test('online board from 16:00 UTC on its start day, practice overall before', () => {
  const phases = [{ slug: 'practice-projects', starts_at: '2026-09-25T15:17:29Z' }, { slug: 'online', starts_at: '2026-10-04T16:00:00Z' }]
  assert.equal(boardPhase(phases, Date.parse('2026-10-04T15:59:59Z'))?.kind, 'practice')
  assert.equal(boardPhase(phases, Date.parse('2026-10-04T16:00:00Z'))?.kind, 'online')
  assert.equal(boardPhase([], Date.now()), null)
})

test('ranks: rank + score, no score yet, or nothing for hidden/teamless', () => {
  const ctx = { ...emptyContext(), board: 'practice' as const, teams: normalizeFriendTeams([{ user_id: 'a', team_id: 't1' }, { user_id: 'b', team_id: 't2' }, { bad: 1 }]),
    ranks: ranksByTeam([{ team_id: 't1', rank: 3, total_score: 7572.1 }]) }
  assert.deepEqual(rankFor(ctx, 'a'), { rank: 3, score: 7572.1 })
  assert.equal(rankFor(ctx, 'b'), 'none')
  assert.equal(rankFor(ctx, 'c'), null)
  assert.equal(rankFor({ ...ctx, board: null }, 'a'), null)
})
