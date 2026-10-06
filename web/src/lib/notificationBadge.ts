// What the header bell, the Dashboard link and the mobile menu dot show (pure, unit-tested). Things waiting for
// the person's answer (team requests/invitations, then friend requests) come first and stay until handled;
// otherwise the bell shows unread team updates.
export interface BadgeCounts { team: number; friends: number; unread: number }

export const pendingTotal = (c: BadgeCounts) => Math.max(0, c.team) + Math.max(0, c.friends)
export const bellCount = (c: BadgeCounts) => pendingTotal(c) || Math.max(0, c.unread)
export const badgeText = (n: number) => (n > 99 ? '99+' : String(n))

/** Where the bell goes: the team inbox first, then the friend requests, else the notification list. */
export function bellTarget(c: BadgeCounts): string {
  if (c.team > 0) return '/team#requests'
  if (c.friends > 0) return '/profile#friends'
  return '/notifications'
}

export function bellLabel(c: BadgeCounts): { en: string; zh: string } {
  const parts = { en: [] as string[], zh: [] as string[] }
  if (c.team > 0) { parts.en.push(`${c.team} team request${c.team === 1 ? '' : 's'}`); parts.zh.push(`${c.team} 条组队请求`) }
  if (c.friends > 0) { parts.en.push(`${c.friends} friend request${c.friends === 1 ? '' : 's'}`); parts.zh.push(`${c.friends} 条好友请求`) }
  if (!parts.en.length) return { en: 'Team notifications', zh: '组队通知' }
  return { en: `${parts.en.join(' and ')} waiting for you`, zh: `${parts.zh.join('、')}待处理` }
}
