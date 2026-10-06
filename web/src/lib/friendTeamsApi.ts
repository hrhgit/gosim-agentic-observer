import { supabase } from './supabase'
import { isProjectBoard, loadCardBoard, loadLeaderboard, loadPhases, type Phase } from './data'
import { boardPhase, normalizeFriendTeams, ranksByTeam, type FriendTeamContext } from './friendTeams.ts'

// Phases change rarely: keep them for 10 minutes. Boards are cached by lib/data (45 s, shared with the board pages).
let phases: { at: number; rows: Phase[] } | null = null
async function cachedPhases() {
  if (!phases || Date.now() - phases.at > 600_000) phases = { at: Date.now(), rows: await loadPhases(true) }
  return phases.rows
}

/** One friend_teams call + one board call for every friend. Any failure leaves that part empty. */
export async function loadFriendTeamContext(): Promise<FriendTeamContext> {
  const [teamsRes, board] = await Promise.all([
    supabase.rpc('friend_teams'),
    (async () => {
      const pick = boardPhase(await cachedPhases())
      if (!pick) return null
      const rows = isProjectBoard(pick.phase) ? (await loadCardBoard(pick.phase.id, null)).rows : await loadLeaderboard(pick.phase.slug, 500)
      return { kind: pick.kind, ranks: ranksByTeam(rows) }
    })().catch(() => null),
  ])
  const teams = teamsRes.error ? {} : normalizeFriendTeams(teamsRes.data)
  return { teams, board: board?.kind ?? null, ranks: board?.ranks ?? {} }
}
