import { onMounted, ref } from 'vue'
import { isSupabaseConfigured } from '../lib/supabase'
import { loadPhases, type Phase } from '../lib/data'

export function usePhases(auto = true) {
  const phases = ref<Phase[]>([])
  const loading = ref(true)
  const error = ref<unknown>(null)
  async function reload() {
    loading.value = true
    error.value = null
    // The public leaderboard must list every phase in LEADERBOARD_SLUGS regardless of which one
    // teams currently submit to, so load the unfiltered set (loadPhases(true)) rather than the
    // current-submission-phase-scoped default.
    try { phases.value = isSupabaseConfigured ? await loadPhases(true) : [] }
    catch (e) { error.value = e; phases.value = [] }
    finally { loading.value = false }
  }
  if (auto) onMounted(reload)
  return { phases, loading, error, reload }
}
