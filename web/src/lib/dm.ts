// Direct messages between friends and the floating friends button. Pure helpers (normalizing RPC answers,
// validation, plain-text link splitting, when the button steps aside), so they are unit-tested.
import type { IncomingRequest } from './friends.ts'
import { linkSegments } from './linkify.ts'

export const DM_MAX_LENGTH = 1000
/** While the panel is open (and the tab visible) it refreshes this often; otherwise the header's 60 s poll keeps the badge. */
export const DM_PANEL_POLL_MS = 5000

export interface DmPerson {
  user_id: string; uid: number | null; name: string; avatar_url: string | null; team_name: string | null; in_team: boolean
  is_friend: boolean; can_send: boolean; last_at: string | null; last_from_me: boolean; last_body: string | null; last_deleted: boolean; unread: number
}
export interface DmOverview { requests: number; incoming: IncomingRequest[]; people: DmPerson[] }
export interface DmMessage { id: number; from_me: boolean; body: string | null; deleted: boolean; created_at: string; read: boolean }
export interface DmThread { can_send: boolean; messages: DmMessage[] }

const obj = (v: unknown) => (v && typeof v === 'object' ? v : {}) as Record<string, unknown>
const list = (v: unknown) => (Array.isArray(v) ? v : [])

export function normalizeOverview(raw: unknown): DmOverview {
  const r = obj(raw)
  const people = list(r.people).map(p => {
    const x = obj(p)
    return {
      user_id: String(x.user_id ?? ''), uid: typeof x.uid === 'number' ? x.uid : null, name: String(x.name ?? ''),
      avatar_url: typeof x.avatar_url === 'string' && x.avatar_url ? x.avatar_url : null,
      team_name: typeof x.team_name === 'string' && x.team_name ? x.team_name : null, in_team: x.in_team === true,
      is_friend: x.is_friend === true, can_send: x.can_send === true,
      last_at: typeof x.last_at === 'string' ? x.last_at : null, last_from_me: x.last_from_me === true,
      last_body: typeof x.last_body === 'string' ? x.last_body : null, last_deleted: x.last_deleted === true,
      unread: Math.max(0, Number(x.unread) || 0),
    }
  }).filter(p => p.user_id)
  return { requests: Math.max(0, Number(r.requests) || 0), incoming: list(r.incoming) as IncomingRequest[], people }
}

export function normalizeThread(raw: unknown): DmThread {
  const r = obj(raw)
  const messages = list(r.messages).map(m => {
    const x = obj(m)
    return { id: Number(x.id), from_me: x.from_me === true, body: typeof x.body === 'string' ? x.body : null,
      deleted: x.deleted === true, created_at: String(x.created_at ?? ''), read: x.read === true }
  }).filter(m => Number.isFinite(m.id))
  return { can_send: r.can_send === true, messages }
}

/** Add newly polled messages to a thread: by id, no duplicates, oldest first. */
export function mergeMessages(current: DmMessage[], incoming: DmMessage[]): DmMessage[] {
  const byId = new Map(current.map(m => [m.id, m]))
  for (const m of incoming) byId.set(m.id, m)
  return [...byId.values()].sort((a, b) => a.id - b.id)
}

/** The text that would be sent, or an error code (i18n key under dm.errors). */
export function checkBody(text: string): { body: string } | { error: 'empty' | 'too_long' } {
  const body = text.replace(/^[\s]+|[\s]+$/g, '')
  if (!body) return { error: 'empty' }
  if ([...body].length > DM_MAX_LENGTH) return { error: 'too_long' }
  return { body }
}

/** dm_send's answer: the new message id, or a thrown error code. */
export function sendOutcome(raw: unknown): number {
  const r = obj(raw)
  if (typeof r.error === 'string' && r.error) throw new Error(r.error)
  if (typeof r.id === 'number') return r.id
  throw new Error('generic')
}

/** Plain text split into text and safe http(s) links (never images, never markup). */
export type DmSegment = { kind: 'text'; text: string } | { kind: 'link'; href: string }
export function messageSegments(body: string | null | undefined): DmSegment[] {
  return linkSegments(body).map(s => (s.kind === 'text' ? s : { kind: 'link' as const, href: s.href }))
    .filter(s => s.kind === 'text' || /^https?:\/\//i.test(s.href))
}

/** The floating button's number: friend requests waiting for an answer + unread messages. '' when none, 99+ cap. */
export function fabBadge(requests: number, unread: number): string {
  const n = Math.max(0, requests | 0) + Math.max(0, unread | 0)
  return n <= 0 ? '' : n > 99 ? '99+' : String(n)
}

/** Popups, the 参赛 guide and toasts the button hides under (anything modal-like on the page, except its own panel). */
export const FAB_HIDE = 'dialog[open]:not(.friends-fab-panel), [role="dialog"], [data-testid="compete-guide"]'
/** Things it must not sit on: it fades out while overlapping one (as the UID label does). */
export const FAB_AVOID = '.register-bar, .section-rail, [data-testid="sky-console"], .flash-item'

/** A message or chat time: HH:MM today, MM-DD HH:MM this year, otherwise YYYY-MM-DD (local time). */
export function shortTime(iso: string | null | undefined, now: Date = new Date()): string {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`
  if (d.toDateString() === now.toDateString()) return hm
  if (d.getFullYear() === now.getFullYear()) return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}`
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}
