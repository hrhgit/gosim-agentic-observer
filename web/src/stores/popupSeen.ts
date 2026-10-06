import { onUnmounted, watch, type Ref } from 'vue'
import { isSupabaseConfigured, supabase } from '../lib/supabase'
import { POPUP_SEEN_KEY, SEEN_AFTER_MS, parseSeenList, rememberSeenList } from '../lib/popupRules'

// Which popups this person has seen (lib/popupRules): localStorage on this device plus, when signed in, the server
// copy (private.popup_seen), so a popup seen on one device or browser stays seen on the others. Either source alone
// is enough: blocked storage falls back to the server, an unreachable server falls back to storage.
const seen = new Set<string>()
let serverFor: string | null = null
let serverLoad: Promise<void> | null = null

function readLocal(): string[] { try { return parseSeenList(localStorage.getItem(POPUP_SEEN_KEY)) } catch { return [] } }
function writeLocal(keys: string[]) {
  try { localStorage.setItem(POPUP_SEEN_KEY, JSON.stringify(rememberSeenList(readLocal(), keys))) } catch { /* blocked: server copy */ }
}
for (const k of readLocal()) seen.add(k)

async function userId(): Promise<string | null> {
  if (!isSupabaseConfigured) return null
  try { const { data } = await supabase.auth.getSession(); return data.session?.user.id ?? null } catch { return null }
}

async function push(keys: string[]) {
  if (!keys.length) return
  for (let attempt = 0; attempt < 2; attempt++) {
    try { const { error } = await supabase.rpc('mark_popups_seen', { p_keys: keys.slice(-50) }); if (!error) return } catch { /* retry once */ }
  }
}

function loadServer(): Promise<void> {
  return userId().then(uid => {
    if (!uid) return
    if (serverFor === uid && serverLoad) return serverLoad
    serverFor = uid
    serverLoad = (async () => {
      try {
        const { data, error } = await supabase.rpc('my_seen_popups')
        if (error || !Array.isArray(data)) return
        const remote = new Set(data.map(String))
        for (const k of remote) seen.add(k)
        writeLocal([...remote])
        // Keys seen on this device before signing in (or while offline) go up once.
        void push(readLocal().filter(k => !remote.has(k)))
      } catch { /* local copy only */ }
    })()
    return serverLoad
  })
}

/** Resolves once the server copy is in (or after a short wait), so a popup is never decided on half the picture. */
export async function seenKeys(): Promise<Set<string>> {
  await Promise.race([loadServer(), new Promise(resolve => setTimeout(resolve, 4000))])
  return seen
}

export function markSeen(keys: string[]) {
  const fresh = keys.filter(k => k && !seen.has(k))
  if (!fresh.length) return
  for (const k of fresh) seen.add(k)
  writeLocal(fresh)
  void userId().then(uid => { if (uid) void push(fresh) })
}

/** Rule 2: once `open` turns true, the popup counts as seen after SEEN_AFTER_MS on screen or as soon as it closes. */
export function useSeenWhileOpen(open: Ref<boolean>, keys: () => string[]) {
  let timer: ReturnType<typeof setTimeout> | undefined
  let shown: string[] = []
  watch(open, isOpen => {
    clearTimeout(timer)
    if (isOpen) {
      shown = keys()
      timer = setTimeout(() => markSeen(shown), SEEN_AFTER_MS)
    } else if (shown.length) { markSeen(shown); shown = [] }
  }, { immediate: true })
  onUnmounted(() => { clearTimeout(timer); if (shown.length) markSeen(shown) })
  return () => { clearTimeout(timer); if (shown.length) markSeen(shown); shown = [] }
}
