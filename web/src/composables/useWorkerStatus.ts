import { computed, onUnmounted, ref } from 'vue'
import { supabase } from '../lib/supabase'

export interface WorkerHeartbeat { worker_id: string; at: string; busy: boolean; queued: number; processed: number; kinds: string[] }

/** Worker liveness (site_settings.worker_heartbeat, written by the evaluator every 30 s) and the position of a
 *  submission in the queue. Polled while `refresh()` keeps being called (the submission page calls it while pending). */
export function useWorkerStatus() {
  const heartbeat = ref<WorkerHeartbeat | null>(null)
  const position = ref<number | null>(null)
  const now = ref(Date.now())
  let timer: number | undefined

  async function refresh(submissionId?: string | number | null) {
    now.value = Date.now()
    const { data } = await supabase.from('site_settings').select('value').eq('key', 'worker_heartbeat').maybeSingle()
    heartbeat.value = (data?.value as WorkerHeartbeat | undefined) ?? null
    if (submissionId != null) {
      const { data: pos } = await supabase.rpc('queue_position', { p_id: Number(submissionId) })
      position.value = typeof pos === 'number' ? pos : null
    } else {
      position.value = null
    }
  }
  function start(submissionId?: string | number | null, intervalMs = 15000) {
    stop()
    void refresh(submissionId)
    timer = window.setInterval(() => { void refresh(submissionId) }, intervalMs)
  }
  function stop() { if (timer) { window.clearInterval(timer); timer = undefined } }
  onUnmounted(stop)

  /** Seconds since the last heartbeat; null when none was ever written. */
  const ageSeconds = computed(() => heartbeat.value ? Math.max(0, Math.round((now.value - new Date(heartbeat.value.at).getTime()) / 1000)) : null)
  /** A worker that has not reported for 3 minutes is treated as offline (a new runner starts within the cron window). */
  const online = computed(() => ageSeconds.value != null && ageSeconds.value < 180)
  return { heartbeat, position, ageSeconds, online, refresh, start, stop }
}
