/** Team places left before registration closes (public.team_capacity, migration 20260927001300). */
export interface TeamCapacity { limit: number; teams: number; remaining: number; full: boolean }

const count = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null

/** Validate the RPC payload; anything malformed is treated as unknown (null), never as full. */
export function parseTeamCapacity(value: unknown): TeamCapacity | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const limit = count(raw.limit), teams = count(raw.teams)
  if (limit === null || teams === null) return null
  return { limit, teams, remaining: Math.max(limit - teams, 0), full: teams >= limit }
}

/** Creating a team is blocked for non-admins once every place is taken. */
export function teamCreationBlocked(capacity: TeamCapacity | null, isAdmin: boolean | null | undefined): boolean {
  return Boolean(capacity?.full && !isAdmin)
}

/** A team limit this high means "no limit" (organizers set 100000): the places line is not shown then. */
export const UNLIMITED_TEAM_LIMIT = 10000

/** Show "Team places: X of Y left" only for a real, not yet reached limit. */
export function showsTeamPlaces(capacity: TeamCapacity | null): boolean {
  return Boolean(capacity && !capacity.full && capacity.limit < UNLIMITED_TEAM_LIMIT)
}
