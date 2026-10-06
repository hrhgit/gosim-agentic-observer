import { reactive, computed } from 'vue'
import type { Session } from '@supabase/supabase-js'
import { supabase, isSupabaseConfigured } from '../lib/supabase'
import { loadCompetition } from './competition'

export interface MeTeam {
  id: string; name: string; slug: string; leader_id: string; invite_code: string
  project_idea: string | null; github_repo: string | null; max_size: number; is_locked: boolean; member_count: number
}
export interface Me {
  id: string; email: string; name: string; nickname: string; github: string | null; affiliation: string | null; role: string | null
  looking_for_team: boolean; locale: string | null; is_admin: boolean; is_banned: boolean; team: MeTeam | null
  astro_level: number; ai_level: number; city: string | null; contact: string | null
  heard_from: string | null; blurb: string | null; show_on_wall: boolean; seeking: string; seeking_count: number
  avatar_url: string
  /** Permanent 9-digit UID (100000001 = first registered); only ever the caller's own. */
  uid?: number | null
}

const state = reactive({
  session: null as Session | null,
  me: null as Me | null,
  ready: false,
  recovery: false,
})

let readyPromise: Promise<void> | null = null

// `me` is fetched on every page mount across the site; dedupe concurrent calls and let a fresh
// fetch stay valid for a short window so navigating between dashboard/profile/submissions pages
// doesn't refetch it every time. Mutations (join team, update profile, ...) call refreshMe()
// directly, which always hits the network and refreshes this window for the next cached read.
const ME_CACHE_MS = 30000
let meFetchedAt = 0
let meInFlight: Promise<Me | null> | null = null

export async function refreshMe(): Promise<Me | null> {
  if (!state.session) { state.me = null; meFetchedAt = 0; return null }
  if (meInFlight) return meInFlight
  meInFlight = (async () => {
    try {
      const { data, error } = await supabase.rpc('me')
      if (!error) { state.me = (data as Me | null) ?? null; meFetchedAt = Date.now() }
    } catch { /* keep the previous snapshot */ }
    return state.me
  })()
  try { return await meInFlight } finally { meInFlight = null }
}

/** Read-only variant for page mounts: reuses the snapshot fetched within the last 30s instead of refetching. */
export async function refreshMeCached(): Promise<Me | null> {
  if (!state.session) { state.me = null; return null }
  if (state.me && Date.now() - meFetchedAt < ME_CACHE_MS) return state.me
  return refreshMe()
}

export function initAuth(): Promise<void> {
  if (readyPromise) return readyPromise
  readyPromise = (async () => {
    if (typeof window !== 'undefined' && /type=recovery/.test(window.location.hash)) state.recovery = true
    try {
      if (isSupabaseConfigured) {
        const { data } = await supabase.auth.getSession()
        state.session = data.session
        if (data.session) await refreshMe()
      }
    } catch { /* offline or misconfigured: treat as logged out */ }
    finally { state.ready = true }
    supabase.auth.onAuthStateChange((event, session) => {
      // Only a session gain or loss changes the beta entry; token refreshes must
      // not force competition refetches.
      const hadSession = Boolean(state.session)
      state.session = session
      if (event === 'PASSWORD_RECOVERY') state.recovery = true
      if (!session) { state.me = null; if (hadSession) window.setTimeout(() => { void loadCompetition(true) }, 0); return }
      // Do not await Supabase calls inside the callback (documented deadlock hazard).
      window.setTimeout(() => { void refreshMe(); if (!hadSession) void loadCompetition(true) }, 0)
    })
  })()
  return readyPromise
}

export async function signOut() {
  try { await supabase.auth.signOut() } catch { /* ignore */ }
  state.session = null
  state.me = null
  meFetchedAt = 0
}

export function useAuth() {
  return {
    state,
    isLoggedIn: computed(() => Boolean(state.session)),
    isAdmin: computed(() => Boolean(state.me?.is_admin)),
    me: computed(() => state.me),
    team: computed(() => state.me?.team ?? null),
    refreshMe,
    refreshMeCached,
    signOut,
    whenReady: () => initAuth(),
  }
}
