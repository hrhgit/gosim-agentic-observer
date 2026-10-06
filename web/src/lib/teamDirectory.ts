// Filtering for the public team directory (`team_directory()` rows). No runtime imports,
// so `npm test` can load it directly.

export interface DirectoryTeam { id: string; name: string; member_count: number; max_size: number; is_locked: boolean }

/** A team someone can still ask to join: open for new members and below its size limit. */
export const isRecruiting = (team: DirectoryTeam) => !team.is_locked && Number(team.member_count) < Number(team.max_size)

/** Recruiting teams unless `showAll`, whose name contains `query` (case-insensitive), in the RPC's order. */
export function filterTeams(teams: DirectoryTeam[], query: string, showAll: boolean): DirectoryTeam[] {
  const needle = query.trim().toLocaleLowerCase()
  return teams.filter(team => (showAll || isRecruiting(team)) && (!needle || team.name.toLocaleLowerCase().includes(needle)))
}

export const directoryCounts = (teams: DirectoryTeam[]) => ({ all: teams.length, recruiting: teams.filter(isRecruiting).length })
