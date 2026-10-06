// Friends and team invitations by UID. The by-UID RPCs answer {status} or {error: code} (so every attempt,
// failed or not, counts towards the daily limit); everything else raises like the other RPCs.

export interface Friend { user_id: string; uid: number | null; name: string; avatar_url: string | null; team_id: string | null; team_name: string | null; in_team: boolean; since: string }
export interface IncomingRequest { id: string; user_id: string; name: string; avatar_url: string | null; created_at: string }
export interface OutgoingRequest { id: string; uid: number; created_at: string }
export interface BlockedUser { user_id: string; name: string }
export interface FriendsData { uid: number | null; daily_limit: number; friends: Friend[]; incoming: IncomingRequest[]; outgoing: OutgoingRequest[]; blocked: BlockedUser[] }

/** A UID as typed: digits only, spaces and a leading "UID" allowed. null unless it is a 9-digit UID. */
export function parseUid(input: string): number | null {
  const digits = input.trim().replace(/^uid[\s:#]*/i, '').replace(/[\s-]/g, '')
  if (!/^[1-9]\d{8}$/.test(digits)) return null
  return Number(digits)
}

/** Turn a by-UID answer into a status (i18n key under friends.result) or throw its error code. */
export function byUidOutcome(data: unknown): string {
  const r = (data && typeof data === 'object' ? data : {}) as { status?: unknown; error?: unknown }
  if (typeof r.error === 'string' && r.error) throw new Error(r.error)
  if (typeof r.status === 'string' && r.status) return r.status
  throw new Error('generic')
}

export function emptyFriends(): FriendsData {
  return { uid: null, daily_limit: 20, friends: [], incoming: [], outgoing: [], blocked: [] }
}

export function normalizeFriends(raw: unknown): FriendsData {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Partial<FriendsData>
  const list = <T>(v: unknown) => (Array.isArray(v) ? v as T[] : [])
  return { uid: typeof r.uid === 'number' ? r.uid : null, daily_limit: Number(r.daily_limit) || 20,
    friends: list<Friend>(r.friends), incoming: list<IncomingRequest>(r.incoming), outgoing: list<OutgoingRequest>(r.outgoing), blocked: list<BlockedUser>(r.blocked) }
}

/** A 「按 UID 找人」 answer: the card plus what the viewer may do, or a thrown neutral error code. */
export interface FoundExtra { uid: number; self: boolean; is_friend: boolean; in_team: boolean }
export function foundExtra(raw: unknown): FoundExtra {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  if (typeof r.error === 'string' && r.error) throw new Error(r.error)
  if (typeof r.id !== 'string' || typeof r.uid !== 'number') throw new Error('not_found')
  return { uid: r.uid, self: r.self === true, is_friend: r.is_friend === true, in_team: r.in_team === true }
}
