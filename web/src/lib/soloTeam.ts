// One-click solo participation: a one-person team named after the participant.
// No runtime imports, so `npm test` can load it directly.

export interface SoloIdentity { nickname?: string | null; name?: string | null; email?: string | null }

/** create_team accepts 2–60 characters (PostgreSQL counts code points, like the spread below). */
const MIN = 2
const MAX = 60
export const SOLO_ATTEMPTS = 3
const chars = (value: string) => [...value]
const clean = (value: string | null | undefined) => (value ?? '').replace(/\s+/g, ' ').trim()

/** Nickname, else account name, else the e-mail local part; always a valid team name. */
export function soloTeamBaseName(me: SoloIdentity): string {
  const candidates = [clean(me.nickname), clean(me.name), clean(clean(me.email).split('@')[0])].filter(Boolean)
  const base = candidates.find(name => chars(name).length >= MIN) ?? (candidates[0] ? `${candidates[0]}-solo` : 'solo')
  return chars(base).slice(0, MAX).join('').trim()
}

/** The name for a given attempt: the base first, then the base with a short suffix, still within 60 characters. */
export function soloTeamName(base: string, attempt: number, suffix: string): string {
  if (attempt === 0) return base
  return `${chars(base).slice(0, MAX - chars(suffix).length - 1).join('').trim()}-${suffix}`
}

/** create_team raises `name_taken`; a race with the unique index surfaces as 23505. */
export function isNameTaken(error: unknown): boolean {
  const e = (error ?? {}) as { message?: unknown; code?: unknown }
  return e.message === 'name_taken' || e.code === '23505'
}

/** Create the solo team, retrying with a random suffix on a name collision; resolves to the name used. */
export async function createSoloTeam(me: SoloIdentity, create: (name: string) => Promise<unknown>, suffix: () => string,
  attempts = SOLO_ATTEMPTS): Promise<string> {
  const base = soloTeamBaseName(me)
  for (let attempt = 0; ; attempt++) {
    const name = soloTeamName(base, attempt, attempt ? suffix() : '')
    try {
      await create(name)
      return name
    } catch (error) {
      if (!isNameTaken(error) || attempt + 1 >= attempts) throw error
    }
  }
}
