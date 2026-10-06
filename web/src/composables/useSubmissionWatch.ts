import { onUnmounted } from 'vue'
import type { RealtimeChannel } from '@supabase/supabase-js'
import { supabase } from '../lib/supabase'

/**
 * Refresh callback on realtime changes to the team's submissions, plus a 5 s poll
 * while `isPending()` is true. Realtime failures are silent (the poll covers them).
 */
export function useSubmissionWatch(onChange: () => void | Promise<void>, isPending: () => boolean) {
  let channel: RealtimeChannel | null = null
  let timer: number | undefined

  function start(teamId: string | null | undefined) {
    stop()
    if (teamId) {
      try {
        channel = supabase
          .channel(`submissions-${teamId}-${Math.random().toString(36).slice(2, 8)}`)
          .on('postgres_changes', { event: '*', schema: 'public', table: 'submissions', filter: `team_id=eq.${teamId}` }, () => { void onChange() })
          .subscribe(() => { /* status changes (including errors) are intentionally ignored */ })
      } catch { channel = null }
    }
    timer = window.setInterval(() => { if (isPending()) void onChange() }, 5000)
  }
  function stop() {
    if (timer) { window.clearInterval(timer); timer = undefined }
    if (channel) { const c = channel; channel = null; void supabase.removeChannel(c).catch(() => { /* ignore */ }) }
  }
  onUnmounted(stop)
  return { start, stop }
}
