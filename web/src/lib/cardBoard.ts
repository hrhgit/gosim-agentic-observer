// Pure row mapping for the leaderboards and the card boards (no Supabase client), so it can be unit-tested.
import type { LeaderboardEntry } from './data'
import { scenarioOrder } from './scenarioLabels.ts'

const numberOrNull = (value: unknown) => value == null ? null : Number(value)
function numberMap(value: unknown): Record<string, number> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).filter(([, v]) => typeof v === 'number') as [string, number][])
}

/** [lowest, highest] of the averaged evaluations, or null. */
function range(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2 || value.some(v => typeof v !== 'number')) return null
  return [value[0] as number, value[1] as number]
}

export function toLeaderboardEntry(row: any, index: number): LeaderboardEntry {
  return {
    rank: Number(row.rank ?? index + 1),
    leader_github: row.leader_github ? String(row.leader_github) : null,
    leader_avatar_url: row.leader_avatar_url ? String(row.leader_avatar_url) : null,
    team_id: String(row.team_id),
    team_name: String(row.team_name ?? '—'),
    team_slug: String(row.team_slug ?? ''),
    total_score: Number(row.total_score ?? 0),
    science_score: Number(row.science_score ?? 0),
    completion_rate: Number(row.completion_rate ?? 0),
    uniformity_score: Number(row.uniformity_score ?? 0),
    base_science: Number(row.base_science ?? 0),
    program_bonus: Number(row.program_bonus ?? 0),
    request_reward: Number(row.request_reward ?? 0),
    coverage_bonus: numberOrNull(row.coverage_bonus),
    coverage_evenness: numberOrNull(row.coverage_evenness),
    penalty_total: Number(row.penalty_total ?? 0),
    completed_tiles: numberOrNull(row.completed_tiles),
    required_missing: numberOrNull(row.required_missing),
    submission_count: Number(row.submission_count ?? 0),
    best_submission_id: numberOrNull(row.best_submission_id),
    kind: row.kind ?? null,
    scored_at: row.scored_at ?? null,
    scenario_slug: row.scenario_slug ? String(row.scenario_slug) : null,
    observer_batch_id: row.observer_batch_id ?? null,
    calibrated: row.calibrated === true,
    raw_total_score: numberOrNull(row.raw_total_score),
    report_reward: Number(row.report_reward ?? 0),
    ...('card_scores' in row || 'overall_score' in row ? {
      card_scores: numberMap(row.card_scores),
      unfinished_cards: Array.isArray(row.unfinished_cards) ? row.unfinished_cards.map(String) : [],
      unfinished: row.unfinished === true,
      overall_score: numberOrNull(row.overall_score),
      overall_rank: numberOrNull(row.overall_rank),
      targets_observed: numberOrNull(row.targets_observed),
      components: numberMap(row.components),
      averaged_runs: numberOrNull(row.averaged_runs),
      score_range: range(row.score_range),
      card_ranges: row.card_ranges && typeof row.card_ranges === 'object' && !Array.isArray(row.card_ranges)
        ? Object.fromEntries(Object.entries(row.card_ranges as Record<string, unknown>)
          .map(([slug, value]) => [slug, range(value)]).filter((entry): entry is [string, [number, number]] => entry[1] !== null))
        : null,
      final_version: row.final_version && typeof row.final_version === 'object'
        ? { chosen: row.final_version.chosen === true, score: numberOrNull(row.final_version.score) } : null,
    } : {}),
  }
}

/**
 * Card boards (migration 20260928004100): a complete-project phase whose settings.board_layout is 'cards' (one board
 * per card) or 'cards_overall' (per card plus the overall mean). Every entry comes from the team's best complete
 * evaluation, or in a phase with repeated evaluations (the hidden final) the mean of its first ones (averaged_runs). Card labels are the scenario names returned by the database, so no card is named in the site.
 */
export type BoardLayout = 'overall' | 'cards' | 'cards_overall'
export interface BoardCard { slug: string; name: string }
export interface CardBoard { layout: BoardLayout; cards: BoardCard[]; scenario: string | null; rows: LeaderboardEntry[] }

/** The tabs of a board, in order; null is the overall tab. Boards without cards have none. */
export function cardBoardTabs(board: Pick<CardBoard, 'layout' | 'cards'> | null): (string | null)[] {
  if (!board || board.layout === 'overall' || !board.cards.length) return []
  return [...(board.layout === 'cards_overall' ? [null] : []), ...board.cards.map(c => c.slug)]
}

/** The requested tab when the board has it, else the first one (overall where there is one). */
export function pickCardTab(board: Pick<CardBoard, 'layout' | 'cards'> | null, wanted: string | null | undefined): string | null {
  const tabs = cardBoardTabs(board)
  const want = wanted ?? null
  return tabs.includes(want) ? want : tabs[0] ?? null
}

export function parseCardBoard(data: any): CardBoard {
  const layout: BoardLayout = data?.layout === 'cards' || data?.layout === 'cards_overall' ? data.layout : 'overall'
  const cards = Array.isArray(data?.cards)
    ? (data.cards as any[]).filter(c => c && c.slug).map(c => ({ slug: String(c.slug), name: String(c.name ?? c.slug) }))
      .sort((a, b) => scenarioOrder(a.slug) - scenarioOrder(b.slug))
    : []
  return { layout, cards, scenario: data?.scenario ? String(data.scenario) : null, rows: (Array.isArray(data?.rows) ? data.rows : []).map(toLeaderboardEntry) }
}
