// During the online competition the practice board stays open next to it (current_competition answers
// practice_phase_id in competition mode). The 参赛 page evaluates in the online phase by default; a person can
// switch to practice, and the choice is remembered per user in this browser.
import type { StorageLike } from './quest'

export type EntryChoice = 'online' | 'practice'
export interface CompetitionState { mode: 'practice' | 'competition'; phaseId: string | null; betaPhaseId: string | null; projectPhaseId: string | null; practicePhaseId: string | null }

export const entryStorageKey = (userId: string) => `survey26.entry.v1.${userId}`

/** The RPC answer, read defensively: unknown or missing keys become null. */
export function parseCompetition(data: unknown): Omit<CompetitionState, 'betaPhaseId'> {
  const d = data && typeof data === 'object' ? data as Record<string, unknown> : {}
  const id = (v: unknown) => typeof v === 'string' ? v : null
  const mode = d.mode === 'competition' ? 'competition' : 'practice'
  return { mode, phaseId: id(d.phase_id), projectPhaseId: id(d.project_phase_id),
    // Only meaningful next to the competition; practice mode keeps using project_phase_id.
    practicePhaseId: mode === 'competition' ? id(d.practice_phase_id) : null }
}

/** Whether the 线上赛 / 练习赛 switch is offered. */
export const offersPracticeSwitch = (s: CompetitionState) => s.mode === 'competition' && !!s.practicePhaseId

export function readEntryChoice(storage: StorageLike | null, userId: string | null | undefined): EntryChoice {
  if (!userId) return 'online'
  try { return storage?.getItem(entryStorageKey(userId)) === 'practice' ? 'practice' : 'online' } catch { return 'online' }
}

/** Where the page starts: once the online phase has ended, always practice (online stays viewable, read-only). */
export function initialEntryChoice(storage: StorageLike | null, userId: string | null | undefined, onlineEnded: boolean): EntryChoice {
  return onlineEnded ? 'practice' : readEntryChoice(storage, userId)
}

export function rememberEntryChoice(storage: StorageLike | null, userId: string | null | undefined, choice: EntryChoice) {
  if (!userId) return
  try { storage?.setItem(entryStorageKey(userId), choice) } catch { /* storage full or blocked */ }
}

/** The phase the evaluate button binds to first. Practice mode is unchanged (beta entry, project board, global). */
export function entryPhaseId(s: CompetitionState, choice: EntryChoice): string | null {
  if (offersPracticeSwitch(s) && choice === 'practice') return s.practicePhaseId
  if (s.mode === 'competition') return s.betaPhaseId ?? s.phaseId ?? s.projectPhaseId
  return s.betaPhaseId ?? s.projectPhaseId ?? s.phaseId
}

/** Phases the workspace may evaluate in (the workflow still keeps only the open ones). */
export function entryPhaseIds(s: CompetitionState): string[] {
  return [s.phaseId, s.betaPhaseId, s.projectPhaseId, s.practicePhaseId].filter((x): x is string => !!x)
}
