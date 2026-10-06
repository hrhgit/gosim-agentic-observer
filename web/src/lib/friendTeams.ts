// Friends' teams in the friend lists: 「邀请入队」 / 「申请加入」 and the team's latest rank. Pure helpers here; the loader
// makes one friend_teams call plus one (client-cached) board call for all friends.

export interface FriendTeam { user_id: string; team_id: string; team_name: string; is_locked: boolean; max_size: number; member_count: number; requested: boolean }
export interface TeamRank { rank: number; score: number }
export type BoardKind = 'online' | 'practice'
export interface FriendTeamContext { teams: Record<string, FriendTeam>; board: BoardKind | null; ranks: Record<string, TeamRank> }

export function emptyContext(): FriendTeamContext { return { teams: {}, board: null, ranks: {} } }

export function normalizeFriendTeams(raw: unknown): Record<string, FriendTeam> {
  const out: Record<string, FriendTeam> = {}
  for (const r of Array.isArray(raw) ? raw : []) {
    const x = (r && typeof r === 'object' ? r : {}) as Record<string, unknown>
    if (typeof x.user_id !== 'string' || typeof x.team_id !== 'string') continue
    out[x.user_id] = { user_id: x.user_id, team_id: x.team_id, team_name: String(x.team_name ?? ''), is_locked: x.is_locked === true,
      max_size: Number(x.max_size) || 0, member_count: Number(x.member_count) || 0, requested: x.requested === true }
  }
  return out
}

/** Which board ranks teams right now: the online board once it has started (16:00 UTC), else the practice overall board. */
export function boardPhase<P extends { slug: string; starts_at: string | null }>(phases: P[], now = Date.now()): { phase: P; kind: BoardKind } | null {
  const online = phases.find(p => p.slug === 'online')
  if (online?.starts_at && now >= new Date(online.starts_at).getTime()) return { phase: online, kind: 'online' }
  const practice = phases.find(p => p.slug === 'practice-projects')
  return practice ? { phase: practice, kind: 'practice' } : null
}

/** Board rows to team -> {rank, score}. */
export function ranksByTeam(rows: Array<{ team_id: string; rank: number; total_score: number }>): Record<string, TeamRank> {
  const out: Record<string, TeamRank> = {}
  for (const r of rows) if (r.team_id && !(r.team_id in out)) out[r.team_id] = { rank: Number(r.rank), score: Number(r.total_score) }
  return out
}

/** What the friend row offers for team-building. */
export type TeamAction =
  | { kind: 'invite' }
  | { kind: 'apply'; team_id: string }
  | { kind: 'apply_disabled'; reason: 'requested' | 'locked' | 'full' }
  | null
export function teamAction(opts: { myTeam: boolean; captain: boolean; friendInTeam: boolean; friendTeam: FriendTeam | undefined; requested?: boolean }): TeamAction {
  const { myTeam, captain, friendInTeam, friendTeam } = opts
  if (myTeam) return captain && !friendInTeam ? { kind: 'invite' } : null
  if (!friendTeam) return null // teamless friend, or a hidden team
  if (friendTeam.requested || opts.requested) return { kind: 'apply_disabled', reason: 'requested' }
  if (friendTeam.is_locked) return { kind: 'apply_disabled', reason: 'locked' }
  if (friendTeam.max_size > 0 && friendTeam.member_count >= friendTeam.max_size) return { kind: 'apply_disabled', reason: 'full' }
  return { kind: 'apply', team_id: friendTeam.team_id }
}

/** The rank line: null when there is nothing to show (no team, or a hidden team), 'none' when the team has no score yet. */
export function rankFor(ctx: FriendTeamContext, userId: string): TeamRank | 'none' | null {
  const ft = ctx.teams[userId]
  if (!ft || !ctx.board) return null
  return ctx.ranks[ft.team_id] ?? 'none'
}
