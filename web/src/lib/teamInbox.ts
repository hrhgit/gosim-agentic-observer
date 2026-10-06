// Team requests and invitations waiting for an answer (pure, unit-tested). Rows come from public.my_team_inbox.
export const TEAM_INBOX_SEEN_KEY = 'sac.team-inbox.popup-seen'
const KEEP = 200

export interface InboxRow {
  id: string; kind: 'invite' | 'request'; direction: 'received' | 'sent'; team_id: string | null; team_name: string
  sender_id?: string; recipient_id?: string
  sender_name: string; recipient_name: string; status: string; created_at: string; updated_at: string
  /** Why it cannot be accepted right now (null = it can). */
  blocked: string | null
}
export interface TeamInbox { received: InboxRow[]; sent: InboxRow[] }

export function normalizeInbox(raw: unknown): TeamInbox {
  const x = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const list = (v: unknown) => (Array.isArray(v) ? v : []).filter(r => r && typeof r === 'object' && typeof (r as InboxRow).id === 'string') as InboxRow[]
  return { received: list(x.received), sent: list(x.sent) }
}

/** Waiting rows the person can act on now (what the badge counts). */
export const actionable = (inbox: TeamInbox) => inbox.received.filter(r => r.status === 'pending' && !r.blocked)

export function parseSeen(raw: string | null): Set<string> {
  try {
    const value = JSON.parse(raw ?? '[]')
    return new Set(Array.isArray(value) ? value.map(String) : [])
  } catch { return new Set() }
}

/** Popup once per new waiting row: the actionable rows not shown in a popup before. */
export const unseenActionable = (inbox: TeamInbox, seen: Set<string>) => actionable(inbox).filter(r => !seen.has(r.id))

export function rememberSeen(raw: string | null, ids: string[]): string {
  return JSON.stringify([...parseSeen(raw)].filter(id => !ids.includes(id)).concat(ids).slice(-KEEP))
}

export function blockedText(reason: string | null): { en: string; zh: string } | null {
  switch (reason) {
    case null: case undefined: case '': return null
    case 'joined_other_team': return { en: 'Already joined another team', zh: '对方已加入其他队伍' }
    case 'already_in_team': return { en: 'You are already in a team', zh: '你已在队伍中' }
    case 'locked': return { en: 'Team is locked (unlock it on the team page to accept)', zh: '队伍已锁定（在队伍设置中取消锁定后可接受）' }
    case 'full': return { en: 'Team is full', zh: '队伍已满' }
    default: return { en: 'Cannot be accepted now', zh: '暂时无法接受' }
  }
}
