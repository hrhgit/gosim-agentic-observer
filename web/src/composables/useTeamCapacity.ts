import { ref } from 'vue'
import { isSupabaseConfigured, supabase } from '../lib/supabase'
import { parseTeamCapacity, type TeamCapacity } from '../lib/teamCapacity'

const capacity = ref<TeamCapacity | null>(null)
let loaded: Promise<void> | null = null

async function fetchCapacity() {
  if (!isSupabaseConfigured) return
  try {
    const { data, error } = await supabase.rpc('team_capacity')
    if (!error) capacity.value = parseTeamCapacity(data)
  } catch { /* unknown capacity never blocks the page; the server still enforces the cap */ }
}

/** Shared, lazily loaded team capacity; reload() after creating a team or on team_limit_reached. */
export function useTeamCapacity() {
  if (!loaded) loaded = fetchCapacity()
  const reload = async () => { loaded = fetchCapacity(); await loaded }
  return { capacity, reload }
}
