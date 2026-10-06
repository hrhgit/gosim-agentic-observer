/** Complete-project evaluation rules shown on the project and records pages. The database enforces them. */
export type EvaluationQuota = { phase_id: string; daily_batches: number; used: number; remaining: number; resets_at?: string
  /** Team-wide daily project preparations (same on every row; follows the evaluation quota, migration 20261004220000). */
  preparations_daily?: number; preparations_used?: number; preparations_remaining?: number }
export type PreparationQuota = { daily: number; used: number; remaining: number; resets_at?: string }

/** The team's daily project preparations, or null when the backend does not report them. */
export function preparationQuota(rows: EvaluationQuota[] | null | undefined): PreparationQuota | null {
  const row = (rows ?? []).find(q => Number.isFinite(Number(q.preparations_daily)) && q.preparations_daily != null)
  if (!row) return null
  const daily = Number(row.preparations_daily), used = Number(row.preparations_used ?? 0)
  return { daily, used, remaining: Number(row.preparations_remaining ?? Math.max(0, daily - used)), resets_at: row.resets_at }
}
type Batch = { revision_id?: string | null; phase_id?: string; status: string; quota_refunded?: boolean }
type Revision = { id: string; status: string; archived_at?: string | null; source_kind?: string; source_location?: string; created_at?: string }
type Project = { title: string; observer_revisions: Revision[] }

/** Earlier evaluations of this version in this phase that used a daily evaluation. */
export function countedEvaluations(batches: Batch[] | null | undefined, revisionId: string, phaseId: string): number {
  return (batches ?? []).filter(b => b.revision_id === revisionId && b.phase_id === phaseId && !b.quota_refunded).length
}

/** A version the team has not evaluated can be withdrawn, except while its preparation job runs. */
export function canWithdraw(revision: Revision, batches: Batch[] | null | undefined): boolean {
  return !revision.archived_at && ['queued', 'reviewable', 'failed', 'approved'].includes(revision.status)
    && !(batches ?? []).some(b => b.revision_id === revision.id)
}

/** Withdrawn versions are hidden unless asked for; a project without visible versions is hidden too. */
export function visibleProjects<P extends Project>(projects: P[] | null | undefined, showWithdrawn: boolean): P[] {
  return (projects ?? []).map(p => ({ ...p, observer_revisions: p.observer_revisions.filter(r => showWithdrawn || !r.archived_at) }))
    .filter(p => p.observer_revisions.length)
}

export function withdrawnCount(projects: Project[] | null | undefined): number {
  return (projects ?? []).reduce((n, p) => n + p.observer_revisions.filter(r => r.archived_at).length, 0)
}

const repository = (url: string) => url.trim().toLowerCase().replace(/\/+$/, '').replace(/\.git$/, '')

/** The same project (title and repository, or title for a ZIP) was submitted in the last few minutes. */
export function recentDuplicate(projects: Project[] | null | undefined, title: string, url: string | null, now = Date.now(), windowMs = 10 * 60_000): boolean {
  return (projects ?? []).some(p => p.title.trim() === title.trim() && p.observer_revisions.some(r =>
    !r.archived_at && r.created_at && now - Date.parse(r.created_at) < windowMs
    && (url === null ? r.source_kind === 'zip' : r.source_kind === 'repository' && repository(r.source_location ?? '') === repository(url))))
}

/** The team's final version in an open formal phase (observer_final_versions). */
export type FinalVersion = {
  phase_id: string; deadline: string | null; locked: boolean
  /** The version the hidden final evaluation will use: the team's choice, else its best evaluation's version. */
  revision_id: string | null; source: 'chosen' | 'best' | null
  chosen_revision_id: string | null; chosen_by: string | null; chosen_at: string | null
  best_batch_id: string | null; best_revision_id: string | null; best_score: number | null
}

/** The final version shown for the selected phase: its own entry, else the first one. */
export function finalVersionFor(finals: FinalVersion[] | null | undefined, phaseId: string): FinalVersion | null {
  return (finals ?? []).find(f => f.phase_id === phaseId) ?? (finals ?? [])[0] ?? null
}

/** How a version relates to the final version: explicitly chosen, the default (best evaluation), or neither. */
export function finalRole(final: FinalVersion | null, revisionId: string): 'chosen' | 'best' | null {
  return final && final.revision_id === revisionId ? final.source : null
}

/** A team can still change its choice: before the deadline, never for the version it already chose. */
export function canChooseFinal(final: FinalVersion | null, revisionId: string, now = Date.now()): boolean {
  if (!final || final.locked || (final.deadline && Date.parse(final.deadline) <= now)) return false
  return final.chosen_revision_id !== revisionId
}

export function canClearFinal(final: FinalVersion | null, now = Date.now()): boolean {
  return !!final && !final.locked && !(final.deadline && Date.parse(final.deadline) <= now) && final.chosen_revision_id != null
}

/** Evaluations in one self-check ("evaluate 3 times and average", observer_create_repeat_batches); the hidden final averages as many. */
export const SELF_CHECK_RUNS = 3

type RepeatBatch = { id: string; status: string; score: number | null; repeat_group?: string | null; repeat_runs?: number | null
  observer_runs: { scenario_id: string; status: string; score: number | null }[] }
type Spread = { mean: number; min: number; max: number }
export type RepeatSummary = {
  group: string; runs: number; scored: number; active: number
  /** Per card over the scored evaluations, in the order the cards first appear. */
  cards: ({ scenario_id: string } & Spread)[]
  /** Over the scored evaluations' combined scores. */
  overall: Spread | null
}

const spread = (values: number[]): Spread | null => values.length
  ? { mean: values.reduce((a, b) => a + b, 0) / values.length, min: Math.min(...values), max: Math.max(...values) } : null

/**
 * The evaluations of each self-check, by repeat group: per card and overall the mean and the range (lowest–highest)
 * over its scored evaluations, the same averaging as the hidden final. Failed evaluations are not averaged.
 */
export function repeatSummaries(batches: RepeatBatch[] | null | undefined): Map<string, RepeatSummary> {
  const groups = new Map<string, RepeatBatch[]>()
  for (const b of batches ?? []) if (b.repeat_group) groups.set(b.repeat_group, [...(groups.get(b.repeat_group) ?? []), b])
  const out = new Map<string, RepeatSummary>()
  for (const [group, own] of groups) {
    const scored = own.filter(b => b.status === 'scored')
    const cards: string[] = []
    for (const b of scored) for (const r of b.observer_runs) if (!cards.includes(r.scenario_id)) cards.push(r.scenario_id)
    out.set(group, {
      group, runs: own[0]!.repeat_runs ?? SELF_CHECK_RUNS, scored: scored.length,
      active: own.filter(b => ['queued', 'running'].includes(b.status)).length,
      cards: cards.flatMap(id => {
        const s = spread(scored.flatMap(b => b.observer_runs.filter(r => r.scenario_id === id && r.score != null).map(r => r.score!)))
        return s ? [{ scenario_id: id, ...s }] : []
      }),
      overall: spread(scored.filter(b => b.score != null).map(b => b.score!)),
    })
  }
  return out
}

/** A self-check needs SELF_CHECK_RUNS of today's evaluations. */
export function canSelfCheck(quota: EvaluationQuota | null | undefined): boolean {
  return quota == null || quota.remaining >= SELF_CHECK_RUNS
}

/** Evaluations in flight that count toward the team's limit: a self-check set counts once. */
export function activeEvaluations(batches: { id: string; status: string; repeat_group?: string | null }[] | null | undefined): number {
  return new Set((batches ?? []).filter(b => ['queued', 'running'].includes(b.status)).map(b => b.repeat_group ?? b.id)).size
}

type FailureBatch = { id: string; status: string; created_at: string
  observer_runs: { id: string; status: string }[] }
/**
 * The newest evaluation when it failed (the batch failed, or it finished with a failed card), else null.
 * `run` is the first failed card, whose logs explain the failure.
 */
export function latestFailure<T extends FailureBatch>(batches: T[] | null | undefined): { batch: T; run: T['observer_runs'][number] | null } | null {
  const newest = [...(batches ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
  if (!newest || ['queued', 'running'].includes(newest.status)) return null
  const run = newest.observer_runs.find(r => r.status === 'failed') ?? null
  return newest.status === 'failed' || run ? { batch: newest, run } : null
}

export type EvaluateBlock = 'busy' | 'phase_closed' | 'no_quota' | 'active_limit' | 'self_check_running' | 'self_check_quota' | null
/** Why the evaluate (or, with selfCheck, the self-check) button is disabled; null when it can be clicked. */
export function evaluateBlock(s: { busy: boolean; phaseEnabled: boolean; quota: EvaluationQuota | null | undefined
  activeAtLimit: boolean; selfCheckActive?: boolean }, selfCheck = false): EvaluateBlock {
  if (!s.phaseEnabled) return 'phase_closed'
  if (s.quota && s.quota.remaining <= 0) return 'no_quota'
  if (s.activeAtLimit) return 'active_limit'
  if (selfCheck && s.selfCheckActive) return 'self_check_running'
  if (selfCheck && !canSelfCheck(s.quota)) return 'self_check_quota'
  if (s.busy) return 'busy'
  return null
}

/** 本次不提供模型: the evaluation ran without the team's model variables (OBSERVER_MODEL_DISABLED=1). */
export function isNoModel(batch: { model_disabled?: boolean | null }): boolean {
  return batch.model_disabled === true
}

/** The evaluation's own facts for a result download (evaluation.json), including whether a model was provided. */
export function evaluationMetadata(batch: { id: string; created_at: string; phase_id: string; revision_id: string | null
  model_disabled?: boolean | null; repeat_group?: string | null }, version: string | null): Record<string, unknown> {
  return { evaluation_id: batch.id, created_at: batch.created_at, phase_id: batch.phase_id,
    revision_id: batch.revision_id, version, model_provided: !isNoModel(batch), model_disabled: isNoModel(batch),
    ...(batch.repeat_group ? { self_check_group: batch.repeat_group } : {}) }
}

/** The combined download's file name; evaluations without a model are marked in it. */
export function evaluationZipName(batch: { id: string; model_disabled?: boolean | null }, date: string): string {
  return `gosim-observer-${batch.id.slice(0, 8)}${isNoModel(batch) ? '-no-model' : ''}-${date}.zip`
}
