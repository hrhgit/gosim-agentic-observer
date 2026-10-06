/**
 * score-report-v3 types and helpers shared by the submission page, the observed sky map and the action timeline.
 * The report is produced by challenge/scoring_core.py; evaluations.summary carries the derived metrics (worker/main.py).
 */
export type ActionOutcome = 'completed' | 'wait' | 'weather_interrupted' | 'geometry_or_night_interrupted' | 'unsafe_observation'
  | 'invalid_observe' | 'invalid_request_tag' | 'outside_tile_window' | 'unknown_slot' | 'stale_decision'
  | 'report_recorded' | 'report_duplicate_ignored' | 'report_correct' | 'report_neutral' | 'report_misreport' | 'report_dropped'
export type OutcomeClass = 'completed' | 'wait' | 'interrupted' | 'unsafe' | 'invalid' | 'report'
export type TerminationReason = 'survey_complete' | 'global_wallclock_expired' | 'agent_error' | 'agent_initialization_error' | 'trace_complete'

export interface ReportSegment {
  slot_id: string; start_utc: string; duration_seconds: number; airmass: number; active_event_ids: string[]
  atmospheric_quality: number; lunar_quality_factor: number; combined_quality: number; quality_band: string; program_matched: boolean
  base_science_score: number; program_bonus_score: number
}
export interface ReportAction {
  decision_id: string; slot_id: string; action: 'observe' | 'wait'; tile_id: string; program: string; request_id: string
  start_utc: string; elapsed_seconds: number; outcome: ActionOutcome | string
  base_science_score: number; program_bonus_score: number; penalty: number; segments: ReportSegment[]
}
export interface ReportRequest {
  request_id: string; status: string; satisfied_tile_count: number; required_tile_count: number; feasible_tile_count: number | null; reward: number; penalty: number
}
export interface ReportTagSettlement {
  tile_id: string; tag: string; report_id: string; settled: string; delta: number
}
export interface ScoreReport {
  schema_version?: string
  score: { total: number; base_science: number; program_bonus: number; request_reward: number; report_reward?: number; coverage_bonus?: number; coverage_evenness?: number; penalties: Partial<Record<PenaltyKey, number>> }
  completion: { completed_tiles: string[]; required_missing: string[]; flexible_by_region: Record<string, number>; flexible_shortfall: Record<string, number> }
  requests: ReportRequest[]
  reports?: { tag_settlements: ReportTagSettlement[]; fault_correct_reports: number; fault_misreports: number; fault_acknowledged_event_ids: string[] }
  wait_seconds: Partial<Record<WaitKey, number>>
  actions: ReportAction[]
  termination_reason: TerminationReason | string
  final_cursor?: { slot_id: string | null; slot_index: number; offset_seconds: number; timestamp_utc: string }
  parameters?: Record<string, unknown>
  input_sha256?: Record<string, string>
}

export type PenaltyKey = 'unsafe_observation' | 'invalid_action' | 'avoidable_wait' | 'required_miss' | 'flexible_shortfall' | 'request_miss' | 'fault_misreport' | 'wrong_tag_report'
export const PENALTY_KEYS: PenaltyKey[] = ['unsafe_observation', 'invalid_action', 'avoidable_wait', 'required_miss', 'flexible_shortfall', 'request_miss', 'fault_misreport', 'wrong_tag_report']
export type WaitKey = 'explicit' | 'implicit' | 'invalid' | 'avoidable' | 'unavailable'
export const WAIT_KEYS: WaitKey[] = ['explicit', 'implicit', 'invalid', 'avoidable', 'unavailable']
export const TERMINATION_REASONS: TerminationReason[] = ['survey_complete', 'global_wallclock_expired', 'agent_error', 'agent_initialization_error', 'trace_complete']

export function outcomeClass(outcome: string | null | undefined, action?: string): OutcomeClass {
  switch (outcome) {
    case 'completed': return 'completed'
    case 'wait': return 'wait'
    case 'weather_interrupted':
    case 'geometry_or_night_interrupted': return 'interrupted'
    case 'unsafe_observation': return 'unsafe'
    case 'report_recorded':
    case 'report_duplicate_ignored':
    case 'report_correct':
    case 'report_neutral':
    case 'report_misreport':
    case 'report_dropped': return 'report'
    case undefined:
    case null:
    case '': return action === 'wait' ? 'wait' : 'completed'
    default: return 'invalid'
  }
}
export const OUTCOME_COLORS: Record<OutcomeClass, string> = { completed: '#315efb', wait: '#333333', interrupted: '#b8860b', unsafe: '#ff3b3b', invalid: '#7a2a2a', report: '#7a4fd0' }

export const penaltyTotal = (report: Pick<ScoreReport, 'score'>) => PENALTY_KEYS.reduce((s, k) => s + Number(report.score.penalties?.[k] ?? 0), 0)
export const actionNet = (a: ReportAction) => Number(a.base_science_score || 0) + Number(a.program_bonus_score || 0) - Number(a.penalty || 0)
/** Night id of a slot id: "N20260907-S001" → "N20260907". */
export const nightOf = (slotId: string | null | undefined) => String(slotId ?? '').split('-S')[0] ?? ''
/** Class of the termination pill: green for a natural end, amber for the clock, red for agent failures. */
export function terminationTone(reason: string | null | undefined): 'ok' | 'warning' | 'failed' | '' {
  if (reason === 'survey_complete' || reason === 'trace_complete') return 'ok'
  if (reason === 'global_wallclock_expired') return 'warning'
  if (reason === 'agent_error' || reason === 'agent_initialization_error') return 'failed'
  return ''
}
