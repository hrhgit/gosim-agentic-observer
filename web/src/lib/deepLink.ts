// Deep links that announcements (and anyone) can use to jump straight to a place, e.g.
//   /compete?tab=settings        opens that tab of the 参赛 workspace (progress | history | settings)
//   /compete#keys                scrolls to 密钥与网络 / 添加模型服务 and highlights it briefly
// Pure lookup here; composables/useDeepLink does the waiting, tab switching, expanding, scrolling and highlighting.
export type CompeteTab = 'progress' | 'history' | 'settings'
export type DeepLink = {
  /** 参赛 tab to open first (simplified layout only; the classic layout has no tabs). */
  tab?: CompeteTab
  /** Buttons to press when present, to reveal the target (e.g. the 上传新版本 form). */
  reveal?: string[]
  /** Targets, best first; the first on the page is scrolled to and highlighted. */
  targets: string[]
}
const tid = (id: string) => `[data-testid="${id}"]`

export const DEEP_LINKS: Record<string, Record<string, DeepLink>> = {
  '/compete': {
    upload: { tab: 'progress', reveal: [tid('project-upload-open')], targets: [tid('project-upload-form'), tid('project-versions')] },
    keys: { tab: 'settings', targets: [tid('model-service'), tid('model-api-settings')] },
    final: { tab: 'settings', targets: [tid('final-version')] },
    phase: { targets: [tid('entry-switch')] },
  },
  '/profile': {
    uid: { targets: ['.friends-uid', tid('my-uid'), tid('friends-panel')] },
    friends: { targets: ['#friends', tid('friends-panel')] },
    'wechat-qr': { targets: ['#wechat-qr', tid('wechat-qr-panel')] },
  },
  '/team': {
    'invite-uid': { targets: ['#invite-uid', tid('team-invite-uid')] },
    requests: { targets: ['#requests', tid('team-inbox')] },
  },
}

/** The deep link for a route path and hash ('#keys'), or null. Trailing slashes and case in the hash are ignored. */
export function deepLinkFor(path: string, hash: string): DeepLink | null {
  const page = DEEP_LINKS[path.replace(/\/+$/, '') || '/']
  const key = decodeURIComponent(hash.replace(/^#/, '')).trim().toLowerCase()
  return (page && key && Object.hasOwn(page, key)) ? page[key]! : null
}

/** ?tab=… on /compete: a known tab name, else null. */
export function tabFromQuery(value: unknown): CompeteTab | null {
  const v = Array.isArray(value) ? value[0] : value
  return v === 'progress' || v === 'history' || v === 'settings' ? v : null
}
