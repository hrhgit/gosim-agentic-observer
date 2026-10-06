import { reactive } from 'vue'
import { SETTLE_MS, pickPopup } from '../lib/popupRules.ts'

// Site popups (the pinned announcement, the Kimi plan popup, the quota-reset notice, the team-request popup, the
// Safari notice) never stack and never follow one another: the ones that ask within SETTLE_MS of the first are
// compared and only the most important one gets the screen for this page load (lib/popupRules). The others are
// still unseen, so they take their turn on a later visit.
const state = reactive({ candidates: [] as string[], chosen: null as string | null, released: false })
let timer: ReturnType<typeof setTimeout> | undefined

export function requestOverlay(name: string, _opts?: { modal?: boolean }) {
  if (state.chosen || state.candidates.includes(name)) return
  state.candidates.push(name)
  if (!timer) timer = setTimeout(() => { state.chosen = pickPopup(state.candidates) }, SETTLE_MS)
}
/** The slot stays used for the rest of this page load. */
export function releaseOverlay(name: string) { if (state.chosen === name) state.released = true }
/** True while `name` holds the screen; reactive, so a watcher can open the overlay when its turn comes. */
export function overlayActive(name: string) { return state.chosen === name && !state.released }
