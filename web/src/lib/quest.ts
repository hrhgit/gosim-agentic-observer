// The dashboard's four-step new-player quest. No runtime imports, so `npm test` can load it directly.

export type QuestMode = 'practice' | 'competition'
export type QuestStepId = 'team' | 'prepare' | 'submit' | 'review'
export const QUEST_STEPS: QuestStepId[] = ['team', 'prepare', 'submit', 'review']

export interface QuestSignals {
  hasTeam: boolean
  /** Kit downloaded / project resources opened (remembered click), or a project already exists. */
  prepared: boolean
  /** The team has at least one submission (Practice) or formal evaluation batch (competition). */
  submitted: boolean
  /** A replay or result was opened (remembered visit). */
  reviewed: boolean
}
export interface QuestStep { id: QuestStepId; done: boolean; current: boolean }
export interface QuestProgress { steps: QuestStep[]; done: number; total: number; current: QuestStepId | null; finished: boolean }

/**
 * Data decides where it can: a submission proves the kit was used, so a later step never waits on
 * an earlier remembered click. The first unfinished step is the one "next" action.
 */
export function questProgress(signals: QuestSignals): QuestProgress {
  const submitted = signals.hasTeam && signals.submitted
  const done: Record<QuestStepId, boolean> = {
    team: signals.hasTeam,
    prepare: signals.prepared || submitted,
    submit: submitted,
    review: submitted && signals.reviewed,
  }
  const current = QUEST_STEPS.find(id => !done[id]) ?? null
  const steps = QUEST_STEPS.map(id => ({ id, done: done[id], current: id === current }))
  return { steps, done: steps.filter(step => step.done).length, total: steps.length, current, finished: current === null }
}

// Steps the database cannot see are remembered per user and per mode in localStorage.
export type QuestFlag = 'prepare' | 'review'
export type QuestFlags = Partial<Record<QuestFlag, boolean>>
export interface StorageLike { getItem(key: string): string | null; setItem(key: string, value: string): void }

export const questStorageKey = (userId: string, mode: QuestMode) => `ao.quest.v1.${userId}.${mode}`

/** Local storage when the browser allows it; blocked storage (private mode, previews) yields null. */
export function browserStorage(): StorageLike | null {
  try { return typeof window === 'undefined' ? null : window.localStorage } catch { return null }
}

export function readQuestFlags(storage: StorageLike | null, userId: string, mode: QuestMode): QuestFlags {
  try {
    const parsed = JSON.parse(storage?.getItem(questStorageKey(userId, mode)) ?? '{}') as Record<string, unknown> | null
    return { prepare: parsed?.prepare === true, review: parsed?.review === true }
  } catch { return {} }
}

export function rememberQuestFlag(storage: StorageLike | null, userId: string, mode: QuestMode, flag: QuestFlag): QuestFlags {
  const flags = { ...readQuestFlags(storage, userId, mode), [flag]: true }
  try { storage?.setItem(questStorageKey(userId, mode), JSON.stringify(flags)) } catch { /* storage full or blocked */ }
  return flags
}
