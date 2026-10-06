import { githubHandle } from './format.ts'

// Profile cards opened from team requests and invitations (public.person_card / public.team_card). Pure, unit-tested.
export interface PersonCard {
  id: string; name: string; avatar_url: string | null; github: string | null; blurb: string | null
  astro_level: number; ai_level: number; seeking: string; seeking_count: number; looking_for_team: boolean
  team_name: string | null; role: string | null; affiliation: string | null; city: string | null; contact: string | null
  /** Storage path of a WeChat QR code this viewer may open (signed on demand), or null. */
  wechat_qr: string | null
}
export interface TeamCardMember { id: string; name: string; avatar_url: string | null; github: string | null; is_leader: boolean; astro_level: number; ai_level: number }
export interface TeamCard { id: string; name: string; project_idea: string | null; max_size: number; is_locked: boolean; members: TeamCardMember[] }

const str = (v: unknown) => (typeof v === 'string' && v.trim() ? v : null)
const num = (v: unknown) => (Number.isFinite(Number(v)) ? Number(v) : 0)
const obj = (v: unknown) => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : null)

export function normalizePersonCard(raw: unknown): PersonCard | null {
  const x = obj(raw)
  if (!x || typeof x.id !== 'string') return null
  return {
    id: x.id, name: str(x.name) ?? '—', avatar_url: str(x.avatar_url), github: str(x.github), blurb: str(x.blurb),
    astro_level: num(x.astro_level), ai_level: num(x.ai_level), seeking: str(x.seeking) ?? '', seeking_count: num(x.seeking_count) || 1,
    looking_for_team: x.looking_for_team === true, team_name: str(x.team_name), role: str(x.role), affiliation: str(x.affiliation),
    city: str(x.city), contact: str(x.contact), wechat_qr: str(x.wechat_qr),
  }
}

export function normalizeTeamCard(raw: unknown): TeamCard | null {
  const x = obj(raw)
  if (!x || typeof x.id !== 'string') return null
  const members = (Array.isArray(x.members) ? x.members : []).map(obj).filter((m): m is Record<string, unknown> => !!m && typeof m.id === 'string')
    .map(m => ({ id: m.id as string, name: str(m.name) ?? '—', avatar_url: str(m.avatar_url), github: str(m.github), is_leader: m.is_leader === true,
      astro_level: num(m.astro_level), ai_level: num(m.ai_level) }))
  return { id: x.id, name: str(x.name) ?? '—', project_idea: str(x.project_idea), max_size: num(x.max_size), is_locked: x.is_locked === true, members }
}

/** GitHub profile link for a username or URL the person entered; null when it is not a plausible username. */
export function githubUrl(value: string | null): string | null {
  const handle = githubHandle(value)
  return handle ? `https://github.com/${handle}` : null
}
