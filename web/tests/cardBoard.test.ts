import assert from 'node:assert/strict'
import test from 'node:test'
import { cardBoardTabs, parseCardBoard, pickCardTab, toLeaderboardEntry } from '../src/lib/cardBoard.ts'

const cards = [{ slug: 'card-a', name: 'Card A' }, { slug: 'card-b', name: 'Card B' }]

test('overall boards have no tabs; card layouts list the cards, with overall first where ranked', () => {
  assert.deepEqual(cardBoardTabs({ layout: 'overall', cards }), [])
  assert.deepEqual(cardBoardTabs({ layout: 'cards', cards }), ['card-a', 'card-b'])
  assert.deepEqual(cardBoardTabs({ layout: 'cards_overall', cards }), [null, 'card-a', 'card-b'])
  // A sealed or not yet open phase returns no card names: no tabs at all.
  assert.deepEqual(cardBoardTabs({ layout: 'cards_overall', cards: [] }), [])
})

test('the requested tab is kept when present, otherwise the first tab is used', () => {
  assert.equal(pickCardTab({ layout: 'cards_overall', cards }, 'card-b'), 'card-b')
  assert.equal(pickCardTab({ layout: 'cards_overall', cards }, null), null)
  assert.equal(pickCardTab({ layout: 'cards_overall', cards }, 'gone'), null)
  assert.equal(pickCardTab({ layout: 'cards', cards }, null), 'card-a')
  assert.equal(pickCardTab({ layout: 'cards', cards }, 'gone'), 'card-a')
  assert.equal(pickCardTab({ layout: 'overall', cards: [] }, 'card-a'), null)
})

test('card board rows keep the observer_board fields and add card scores and components', () => {
  const board = parseCardBoard({
    layout: 'cards_overall', scenario: null, cards: [...cards, { slug: 'no-name' }, null],
    rows: [{ rank: 1, team_id: 't1', team_name: 'Team', total_score: 40, submission_count: 2, kind: 'observer',
      card_scores: { 'card-a': 30, 'card-b': 50, bad: 'x' }, overall_score: 40, overall_rank: 1, targets_observed: null,
      components: { sum_best_scores: 50, required_penalty: -6, note: 'text' }, required_missing: 1 }],
  })
  assert.equal(board.layout, 'cards_overall')
  assert.deepEqual(board.cards.map(c => c.name), ['Card A', 'Card B', 'no-name'])
  const [row] = board.rows
  assert.deepEqual(row!.card_scores, { 'card-a': 30, 'card-b': 50 })
  assert.deepEqual(row!.components, { sum_best_scores: 50, required_penalty: -6 })
  assert.equal(row!.targets_observed, null)
  assert.equal(row!.required_missing, 1)
  assert.equal(parseCardBoard({ layout: 'something-else' }).layout, 'overall')
})

test('parseCardBoard orders real v4 card slugs canonically (alpha/beta/gamma/delta, A-D), not by slug text', () => {
  const practice = parseCardBoard({ layout: 'cards_overall', cards: [
    { slug: 'v4-practice-delta', name: 'Practice card δ' }, { slug: 'v4-practice-gamma', name: 'Practice card γ' },
    { slug: 'v4-practice-alpha', name: 'Practice card α' }, { slug: 'v4-practice-beta', name: 'Practice card β' },
  ] })
  assert.deepEqual(practice.cards.map(c => c.slug), ['v4-practice-alpha', 'v4-practice-beta', 'v4-practice-gamma', 'v4-practice-delta'])
  const formal = parseCardBoard({ layout: 'cards', cards: [
    { slug: 'v4-c', name: 'Card C' }, { slug: 'v4-a', name: 'Card A' }, { slug: 'v4-d', name: 'Card D' }, { slug: 'v4-b', name: 'Card B' },
  ] })
  assert.deepEqual(formal.cards.map(c => c.slug), ['v4-a', 'v4-b', 'v4-c', 'v4-d'])
})

test('v3 board rows map exactly as before (no card fields)', () => {
  const row = toLeaderboardEntry({ team_id: 't', total_score: '12.5', completed_tiles: null, coverage_bonus: 0 }, 3)
  assert.equal(row.rank, 4)
  assert.equal(row.total_score, 12.5)
  assert.equal(row.completed_tiles, null)
  assert.equal(row.coverage_bonus, 0)
  assert.ok(!('card_scores' in row) && !('components' in row))
})

test('the uploaded avatar flows through alongside the github handle, both null when absent', () => {
  const withAvatar = toLeaderboardEntry({ team_id: 't', total_score: 1, leader_github: 'octocat', leader_avatar_url: 'https://x/avatar.png' }, 0)
  assert.equal(withAvatar.leader_github, 'octocat')
  assert.equal(withAvatar.leader_avatar_url, 'https://x/avatar.png')
  const bare = toLeaderboardEntry({ team_id: 't', total_score: 1 }, 0)
  assert.equal(bare.leader_github, null)
  assert.equal(bare.leader_avatar_url, null)
})

test('hidden final rows keep the cards the team itself failed (shown as 0)', () => {
  const board = parseCardBoard({
    layout: 'cards_overall', scenario: null, cards,
    rows: [{ rank: 1, team_id: 't1', team_name: 'Team', total_score: 25, card_scores: { 'card-a': 50, 'card-b': 0 },
      unfinished_cards: ['card-b'], overall_score: 25, overall_rank: 1 }],
  })
  assert.deepEqual(board.rows[0]!.card_scores, { 'card-a': 50, 'card-b': 0 })
  assert.deepEqual(board.rows[0]!.unfinished_cards, ['card-b'])
  const cardRow = toLeaderboardEntry({ team_id: 't1', total_score: 0, unfinished: true, overall_score: 25 }, 0)
  assert.equal(cardRow.unfinished, true)
  const plain = toLeaderboardEntry({ team_id: 't2', total_score: 10, card_scores: { 'card-a': 10 } }, 0)
  assert.deepEqual([plain.unfinished_cards, plain.unfinished], [[], false])
})

test('rows carry the averaged evaluation count and the final-version mark', async () => {
  const { parseCardBoard } = await import('../src/lib/cardBoard.ts')
  const board = parseCardBoard({ layout: 'cards_overall', cards: [], rows: [
    { team_id: 't1', total_score: 5, card_scores: {}, averaged_runs: 3, final_version: { chosen: true, score: 7123.4 } },
    { team_id: 't2', total_score: 4, card_scores: {}, final_version: { chosen: false, score: null } },
    { team_id: 't3', total_score: 3, card_scores: {} },
  ] })
  assert.deepEqual(board.rows.map(r => [r.averaged_runs, r.final_version]), [
    [3, { chosen: true, score: 7123.4 }], [null, { chosen: false, score: null }], [null, null]])
})

test('averaged rows carry the range of their evaluations, overall and per card', () => {
  const row = toLeaderboardEntry({ team_id: 't1', total_score: 47.5, card_scores: { 'card-a': 70 }, averaged_runs: 3,
    score_range: [45, 52.5], card_ranges: { 'card-a': [60, 90], 'card-b': 'bad' } }, 0)
  assert.deepEqual([row.score_range, row.card_ranges], [[45, 52.5], { 'card-a': [60, 90] }])
  const single = toLeaderboardEntry({ team_id: 't2', total_score: 1, card_scores: {}, score_range: null, card_ranges: null }, 0)
  assert.deepEqual([single.score_range, single.card_ranges], [null, null])
})

test('the leaderboard page adds the final tab last; the home board keeps the three public boards', async () => {
  const { LEADERBOARD_PAGE_SLUGS, LEADERBOARD_SLUGS, LEADERBOARD_TAB_LABEL_KEYS } = await import('../src/lib/leaderboardBoards.ts')
  assert.deepEqual([...LEADERBOARD_SLUGS], ['practice-projects', 'practice', 'online'])
  assert.deepEqual([...LEADERBOARD_PAGE_SLUGS], ['practice-projects', 'practice', 'online', 'final-hidden'])
  assert.equal(LEADERBOARD_TAB_LABEL_KEYS['final-hidden'], 'leaderboard.tabs.final')
})
