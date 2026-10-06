import { ref } from 'vue'
import { isSupabaseConfigured } from '../lib/supabase'
import { loadRegistrationOpen } from '../lib/data'

const registrationOpen = ref(true)
let loaded: Promise<void> | null = null

export function useRegistrationOpen() {
  if (!loaded) {
    loaded = (async () => {
      if (!isSupabaseConfigured) return
      registrationOpen.value = await loadRegistrationOpen()
    })()
  }
  const reload = async () => { registrationOpen.value = await loadRegistrationOpen() }
  return { registrationOpen, reload }
}
