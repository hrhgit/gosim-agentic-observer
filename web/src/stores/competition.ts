import { computed, reactive, readonly } from 'vue'
import { isSupabaseConfigured, supabase } from '../lib/supabase'
import { browserStorage } from '../lib/quest'
import { entryPhaseId, initialEntryChoice, parseCompetition, rememberEntryChoice, type EntryChoice } from '../lib/entryPhase'
const state = reactive({ mode: 'practice' as 'practice'|'competition', phaseId: null as string|null, betaPhaseId: null as string|null, projectPhaseId: null as string|null,
  // Competition mode only: the practice board that stays open next to the online phase.
  practicePhaseId: null as string|null })
let fetched = 0, pending: Promise<void>|null = null
export const competition = readonly(state)
// 线上赛 / 练习赛 on the 参赛 page during the competition, remembered per user in this browser.
const entry = reactive({ userId: null as string|null, choice: 'online' as EntryChoice })
export const entryChoice = computed(() => entry.choice)
export const entryPhase = computed(() => entryPhaseId(state, entry.choice))
export function useEntryFor(userId: string|null|undefined, onlineEnded = false) {
  entry.userId = userId ?? null
  entry.choice = initialEntryChoice(browserStorage(), userId, onlineEnded)
}
export function chooseEntry(choice: EntryChoice) {
  entry.choice = choice
  rememberEntryChoice(browserStorage(), entry.userId, choice)
}
export async function loadCompetition(force=false) {
  if (pending) { await pending; if (!force) return state }
  if (!force && Date.now()-fetched<15000) return state
  pending=(async () => {
    if (!isSupabaseConfigured) return
    const {data,error}=await supabase.rpc('current_competition')
    if (!error && data) {
      // Practice mode: project_phase_id is the separate complete-project board next to CSV practice.
      Object.assign(state, parseCompetition(data))
      fetched=Date.now()
    }
    // Team-restricted beta entry: the RPC is granted to signed-in users only and
    // answers with a phase just for its access team. The
    // previous value stays until the fresh answer avoids entry flicker.
    let beta: string|null = null
    const {data:{session}}=await supabase.auth.getSession()
    if (session) {
      const answer=await supabase.rpc('my_observer_phase')
      if (!answer.error && typeof answer.data==='string') beta=answer.data
    }
    state.betaPhaseId=beta
  })()
  try { await pending } finally { pending=null }
  return state
}
