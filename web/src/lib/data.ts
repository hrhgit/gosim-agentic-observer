import { parseCompeteUi, type CompeteUiSetting } from './competeUi'
import { supabase } from './supabase'
import { normalizeKimiPlanStatus, type KimiPlanStatus } from './kimiPlan'
import { normalizeQuotaResetNotice, type QuotaResetNotice } from './quotaReset'
import { parseCardBoard, pickCardTab, toLeaderboardEntry, type CardBoard } from './cardBoard'
import { cached, invalidatePrefix } from './requestCache'
import { scenarioOrder } from './scenarioLabels'

// Board reads are shared by the home page, the leaderboard page and their own 60s poll timers;
// a short cache (and in-flight dedupe) keeps a burst of callers within this window to one request.
const BOARD_CACHE_MS = 45000

export type PhaseStatus = 'open' | 'upcoming' | 'closed' | 'disabled'
export type LeaderboardMode = 'live' | 'frozen' | 'hidden' | 'published'

export interface Scenario {
  id: string; slug: string; name: string; description: string | null
  weather_public: boolean; tiles_public: boolean; forecasts_public: boolean; events_public: boolean; is_active: boolean
  n_slots: number | null; n_nights: number | null; n_tiles: number | null; n_targets: number | null; n_requests: number | null
  global_wallclock_seconds: number | null; contract: string | null; created_at: string
  /** Withheld from participants: `seed`, `checksum` and `manifest` would let anyone rebuild a hidden-weather
   *  scenario locally, so anon/authenticated have no column privilege on them. Only `admin_scenarios()` fills
   *  these in — everywhere else they are undefined. */
  seed?: number | null; checksum?: string | null; manifest?: Record<string, unknown> | null
}

/** The scenario columns participants are allowed to read (see migration 20260915000100_hide_scenario_seeds). */
export const SCENARIO_PUBLIC_COLUMNS =
  'id, slug, name, description, weather_public, forecasts_public, events_public, tiles_public, is_active, ' +
  'n_slots, n_nights, n_tiles, n_targets, n_requests, global_wallclock_seconds, contract, created_at'

/** Files of a scenario directory in the `scenarios` bucket (`<slug>/config/<file>` and `<slug>/outputs/reference/<file>`). */
export type ScenarioFileGroup = 'config' | 'data' | 'weather' | 'forecasts' | 'events'
export interface ScenarioFile { key: string; name: string; group: ScenarioFileGroup; flag?: 'weather_public' | 'forecasts_public' | 'events_public' }
export const SCENARIO_FILES: ScenarioFile[] = [
  ...['scenario_config.json', 'calendar_config.json', 'tile_config.json', 'weather_config.json', 'request_config.json', 'workflow_config.json', 'score_config.json']
    .map(name => ({ key: `config/${name}`, name, group: 'config' as const })),
  ...['night_calendar.csv', 'slots.csv', 'tiles.csv', 'targets.csv', 'tile_windows.csv', 'observation_requests.csv', 'observation_request_tiles.csv',
    'scenario_manifest.json', 'calendar_metadata.json', 'catalog_metadata.json', 'observation_request_metadata.json']
    .map(name => ({ key: `outputs/reference/${name}`, name, group: 'data' as const })),
  { key: 'outputs/reference/weather.csv', name: 'weather.csv', group: 'weather', flag: 'weather_public' },
  { key: 'outputs/reference/weather_metadata.json', name: 'weather_metadata.json', group: 'weather', flag: 'weather_public' },
  { key: 'outputs/reference/weather_forecasts.csv', name: 'weather_forecasts.csv', group: 'forecasts', flag: 'forecasts_public' },
  { key: 'outputs/reference/weather_events.csv', name: 'weather_events.csv', group: 'events', flag: 'events_public' },
]
export const scenarioFileVisible = (s: Scenario, f: ScenarioFile) => !f.flag || Boolean(s[f.flag])
export const scenarioObjectKey = (slug: string, file: string) => `${slug}/${file}`
export interface Phase {
  id: string; slug: string; name_en: string; name_zh: string; description_en: string | null; description_zh: string | null
  sort_order: number; starts_at: string | null; ends_at: string | null; allow_results: boolean; allow_agents: boolean
  daily_limit: number; leaderboard_mode: LeaderboardMode; counts_for_final: boolean; is_active: boolean
  scenarios: Scenario[]; status: PhaseStatus
  observer_settings?: { projects_enabled: boolean; local_sessions_enabled: boolean; daily_batches: number; sealed?: boolean; repeat_runs?: number } | null
}
export interface Announcement {
  id: string; title_en: string; title_zh: string; body_en: string | null; body_zh: string | null
  level: 'info' | 'warning' | 'success'; is_pinned: boolean; is_published: boolean; created_at: string
  /** Bumped by an organizer to pop the announcement up again for everyone (missing before the migration = 1). */
  notify_version?: number
}
export interface LeaderboardEntry {
  rank: number; team_id: string; team_name: string; team_slug: string; total_score: number; science_score: number
  completion_rate: number; uniformity_score: number
  base_science: number; program_bonus: number; request_reward: number; coverage_bonus: number | null; coverage_evenness: number | null; penalty_total: number; completed_tiles: number | null; required_missing: number | null
  submission_count: number; best_submission_id: number | null; kind: string | null; scored_at: string | null; leader_github: string | null
  leader_avatar_url: string | null
  /** The scenario this board ranks; null on the final board, which averages every scenario of the phase. */
  scenario_slug: string | null
  observer_batch_id?: string | null; report_reward?: number
  calibrated?: boolean; raw_total_score?: number | null
  /** Card boards (observer_card_board) only: per-card scores on the overall tab, the overall score and rank on a card tab. */
  card_scores?: Record<string, number> | null; overall_score?: number | null; overall_rank?: number | null
  /** Hidden final only: cards the team itself failed, shown as 0 (overall tab: their slugs; card tab: this card). */
  unfinished_cards?: string[]; unfinished?: boolean
  targets_observed?: number | null; components?: Record<string, number> | null
  /** Phases that average repeated evaluations (the hidden final): how many evaluations the row averages. */
  averaged_runs?: number | null
  /** With averaged_runs: the lowest and highest of those evaluations (this tab's score), and per card on the overall tab. */
  score_range?: [number, number] | null; card_ranges?: Record<string, [number, number]> | null
  /** The formal phase where teams choose a final version: whether this team chose one, and that version's score on this tab. */
  final_version?: { chosen: boolean; score: number | null } | null
}

export interface PhaseCopy {
  /** Human-facing summary without operational numbers baked in. */
  description: string
  /** Operational facts rendered separately from the description. */
  facts: string[]
}

/**
 * Keep user-facing phase copy consistent with the database row.
 *
 * The description fields are prose only; submission limits, result/package support,
 * and leaderboard visibility are derived from structured columns so future changes
 * only need to happen in one place.
 */
export function phaseCopy(
  phase: Pick<Phase, 'description_en' | 'description_zh' | 'allow_results' | 'allow_agents' | 'daily_limit' | 'leaderboard_mode' | 'observer_settings'>,
  locale: string,
): PhaseCopy {
  const description = (locale === 'zh' ? phase.description_zh : phase.description_en)
    ?? (locale === 'zh' ? phase.description_en : phase.description_zh)
    ?? ''
  const online=phase.observer_settings
  // The hidden final: no daily evaluations; each team's final version is evaluated repeat_runs times and averaged.
  if (online?.sealed && online.projects_enabled) {
    const n = Math.max(1, Number(online.repeat_runs ?? 1))
    return {description,facts:locale==='zh'
      ? ['完整项目云端评测',`每队最终版本评测 ${n} 次`,...(n>1?[`每张卡取 ${n} 次平均，总分为各卡平均`]:[])]
      : ['Complete project cloud evaluation',`Each team's final version evaluated ${n} time${n>1?'s':''}`,...(n>1?[`Each card is the mean of ${n} evaluations; overall is the mean over the cards`]:[])]}
  }
  if (online?.projects_enabled || online?.local_sessions_enabled) return {description,facts:locale==='zh'
    ? [...(online.projects_enabled?['完整项目云端评测']:[]),...(online.local_sessions_enabled?['本地运行并提交 CSV']:[]),`每队每天 ${online.daily_batches} 次`,'同一次评测的多个场景取平均']
    : [...(online.projects_enabled?['Complete project cloud evaluation']:[]),...(online.local_sessions_enabled?['Local run with CSV submission']:[]),`${online.daily_batches} evaluations per team per day`,'Average across all scenarios in one evaluation']}
  const facts = locale === 'zh'
    ? [
        `结果文件 ${phase.allow_results ? '允许' : '不允许'}`,
        ...(phase.allow_agents ? ['智能体程序包 允许'] : []),
        `每队每天 ${phase.daily_limit} 次`,
        `榜单 ${({ live: '实时', frozen: '已冻结', hidden: '隐藏', published: '已公布' } as Record<string, string>)[phase.leaderboard_mode] ?? phase.leaderboard_mode}`,
      ]
    : [
        `results files ${phase.allow_results ? 'allowed' : 'not allowed'}`,
        ...(phase.allow_agents ? ['agent packages allowed'] : []),
        `${phase.daily_limit} submissions per team per day`,
        `leaderboard ${phase.leaderboard_mode}`,
      ]
  return { description, facts }
}

export function phaseStatus(p: { is_active: boolean; starts_at: string | null; ends_at: string | null }, now = Date.now()): PhaseStatus {
  if (!p.is_active) return 'disabled'
  if (p.starts_at && now < new Date(p.starts_at).getTime()) return 'upcoming'
  if (p.ends_at && now > new Date(p.ends_at).getTime()) return 'closed'
  return 'open'
}

export async function loadPhases(all = false): Promise<Phase[]> {
  const { data, error } = await supabase
    .from('phases')
    .select(`*, observer_settings:observer_phase_settings(projects_enabled,local_sessions_enabled,daily_batches,sealed,repeat_runs), phase_scenarios(scenario_id, scenarios(${SCENARIO_PUBLIC_COLUMNS}))`)
    .order('sort_order', { ascending: true })
  if (error) throw error
  const rows = ((data ?? []) as any[]).map(row => {
    const { phase_scenarios, ...rest } = row
    const scenarios = ((phase_scenarios ?? []) as any[]).map(link => link.scenarios).filter(Boolean) as Scenario[]
    scenarios.sort((a, b) => a.slug.localeCompare(b.slug))
    return { ...rest, scenarios, status: phaseStatus(rest) } as Phase
  })
  if (all) return rows
  const { loadCompetition } = await import('../stores/competition')
  const current = await loadCompetition()
  return rows.filter(p => p.id===current.projectPhaseId || isFinalBoard(p) || (current.phaseId ? p.id===current.phaseId : p.slug===(current.mode==='practice'?'practice':'online')))
}

/** The hidden final phase: readable only once organizers publish its results (the database hides it before). */
export function isFinalBoard(p: Pick<Phase, 'observer_settings'>): boolean {
  return !!p.observer_settings?.sealed
}

/** The online board on the fixed formal scenarios: live, but it does not decide the final ranking. */
export function isPublicFormalBoard(p: Pick<Phase, 'slug' | 'observer_settings'>): boolean {
  return p.slug === 'online' && !isFinalBoard(p)
}

/** The "main" phase: the counts_for_final one that is open/closed, else the first open one, else the first. */
export function mainPhase(phases: Phase[]): Phase | null {
  return phases.find(p => p.counts_for_final && (p.status === 'open' || p.status === 'closed'))
    ?? phases.find(p => p.status === 'open')
    ?? phases[0] ?? null
}

/** Home page board: the main phase once the competition counts, otherwise the complete-project practice board. */
export function homeBoardPhase(phases: Phase[]): Phase | null {
  const main = mainPhase(phases)
  if (main?.counts_for_final) return main
  return phases.find(p => p.slug === 'practice-projects' && p.status === 'open') ?? main
}

export async function loadScenarios(): Promise<Scenario[]> {
  const { data, error } = await supabase.from('scenarios').select(SCENARIO_PUBLIC_COLUMNS).order('slug')
  if (error) throw error
  return (data ?? []) as unknown as Scenario[]
}

/** Admin-only view of the same rows, including the withheld seed/checksum/manifest. */
export async function loadScenariosAsAdmin(): Promise<Scenario[]> {
  const { data, error } = await supabase.rpc('admin_scenarios')
  if (error) throw error
  return (data ?? []) as Scenario[]
}

export async function loadAnnouncements(limit?: number): Promise<Announcement[]> {
  let query = supabase.from('announcements').select('*').eq('is_published', true)
    .order('is_pinned', { ascending: false }).order('created_at', { ascending: false })
  if (limit) query = query.limit(limit)
  const { data, error } = await query
  if (error) throw error
  return (data ?? []) as Announcement[]
}

export interface PublicSettings { registrationOpen: boolean; registrationDeadline: string | null }

export async function loadPublicSettings(): Promise<PublicSettings> {
  const fallback: PublicSettings = { registrationOpen: true, registrationDeadline: null }
  try {
    const { data, error } = await supabase.from('site_settings').select('key, value')
      .in('key', ['registration_open', 'registration_deadline'])
    if (error || !data) return fallback
    const map = Object.fromEntries((data as { key: string; value: unknown }[]).map(row => [row.key, row.value]))
    const openFlag = !(map.registration_open === false || map.registration_open === 'false')
    const deadline = typeof map.registration_deadline === 'string' ? map.registration_deadline : null
    const beforeDeadline = !deadline || Date.now() < Date.parse(deadline)
    return {
      registrationOpen: openFlag && beforeDeadline,
      registrationDeadline: deadline,
    }
  } catch { return fallback }
}

export async function loadRegistrationOpen(): Promise<boolean> {
  return (await loadPublicSettings()).registrationOpen
}

/**
 * Scenarios a phase's board can be switched between. Results files cover one scenario each, so a practice
 * board ranks one scenario at a time (longest first, the database's default); the final board averages
 * every scenario and has no switch.
 */
export function boardScenarios(phase: Pick<Phase, 'counts_for_final' | 'scenarios' | 'observer_settings'> | null): Scenario[] {
  if (!phase || phase.observer_settings?.projects_enabled || phase.observer_settings?.local_sessions_enabled || phase.counts_for_final || phase.scenarios.length < 2) return []
  return [...phase.scenarios].sort((a, b) => scenarioOrder(a.slug) - scenarioOrder(b.slug) || (b.n_nights ?? 0) - (a.n_nights ?? 0) || a.slug.localeCompare(b.slug))
}

export async function loadLeaderboard(phaseSlug: string | null, limit = 500, scenarioSlug: string | null = null, observerPhaseId?: string): Promise<LeaderboardEntry[]> {
  const key = `leaderboard:${observerPhaseId ?? ''}:${phaseSlug ?? ''}:${scenarioSlug ?? ''}:${limit}`
  const rows = await cached(key, BOARD_CACHE_MS, async () => {
    const { data, error } = observerPhaseId
      ? await supabase.rpc('observer_board', { p_phase: observerPhaseId, p_limit: limit })
      : await supabase.rpc('leaderboard', { p_phase_slug: phaseSlug, p_limit: limit, p_scenario_slug: scenarioSlug })
    if (error) throw error
    return (data ?? []) as any[]
  })
  return rows.map(toLeaderboardEntry)
}

export { toLeaderboardEntry, cardBoardTabs, pickCardTab, parseCardBoard, type BoardLayout, type BoardCard, type CardBoard } from './cardBoard'

/** Clear the leaderboard/card-board cache so a profile or team change the user just made shows up on their
 * own next visit instead of waiting out the shared BOARD_CACHE_MS window. */
export function invalidateBoardCache() {
  invalidatePrefix('leaderboard:')
  invalidatePrefix('card_board:')
}

async function fetchCardBoard(phaseId: string, scenarioSlug: string | null, limit: number): Promise<CardBoard> {
  const key = `card_board:${phaseId}:${scenarioSlug ?? ''}:${limit}`
  return cached(key, BOARD_CACHE_MS, async () => {
    const { data, error } = await supabase.rpc('observer_card_board', { p_phase: phaseId, p_scenario_slug: scenarioSlug, p_limit: limit })
    // Until the card-board migration is deployed, complete-project phases keep the existing board.
    if (error) return { layout: 'overall', cards: [], scenario: null, rows: await loadLeaderboard(null, limit, null, phaseId) }
    return parseCardBoard(data)
  })
}

/** A complete-project board, on the requested card tab when the phase has cards (see pickCardTab). */
export async function loadCardBoard(phaseId: string, wanted: string | null = null, limit = 500): Promise<CardBoard> {
  const board = await fetchCardBoard(phaseId, wanted, limit)
  const tab = pickCardTab(board, wanted)
  return board.layout !== 'overall' && tab !== (wanted ?? null) ? fetchCardBoard(phaseId, tab, limit) : board
}

/** Whether a phase ranks complete projects (the observer boards) rather than CSV submissions. */
export const isProjectBoard = (phase: Pick<Phase, 'observer_settings'> | null) =>
  !!(phase?.observer_settings?.projects_enabled || phase?.observer_settings?.local_sessions_enabled)

export const SUBMISSION_SELECT = '*, phases(slug,name_en,name_zh), scenarios(slug,name), evaluations(*, scenarios(slug,name,tiles_public,weather_public,global_wallclock_seconds,n_tiles,n_nights))'
export const PENDING_STATUSES = new Set(['queued', 'running'])

// --- sponsor API credits (redeem codes) -----------------------------------
export interface RedeemProvider { provider: string; available: number; claimed_by_my_team: boolean }
export interface RedeemCode { provider: string; code: string; note: string; assigned_at: string | null }
export interface CreditsNote { en: string; zh: string }

export async function loadRedeemProviders(): Promise<RedeemProvider[]> {
  const { data, error } = await supabase.rpc('redeem_providers')
  if (error) throw error
  return ((data ?? []) as any[]).map(row => ({ provider: String(row.provider), available: Number(row.available ?? 0), claimed_by_my_team: Boolean(row.claimed_by_my_team) }))
}

export async function loadMyRedeemCodes(): Promise<RedeemCode[]> {
  const { data, error } = await supabase.rpc('my_redeem_codes')
  if (error) throw error
  return ((data ?? []) as any[]).map(row => ({ provider: String(row.provider), code: String(row.code), note: String(row.note ?? ''), assigned_at: row.assigned_at ?? null }))
}

/** Optional explanatory text above the credits panel (site_settings.credits_note = {en, zh}); empty strings when unset. */
export async function loadCreditsNote(): Promise<CreditsNote> {
  try {
    const { data, error } = await supabase.from('site_settings').select('value').eq('key', 'credits_note').maybeSingle()
    if (error || !data) return { en: '', zh: '' }
    const value = ((data as { value: unknown }).value ?? {}) as Record<string, unknown>
    return { en: typeof value.en === 'string' ? value.en : '', zh: typeof value.zh === 'string' ? value.zh : '' }
  } catch { return { en: '', zh: '' } }
}

// --- Kimi Coding Plan (captain claims one code once the team has a practice score) ---
export async function loadKimiPlanStatus(): Promise<KimiPlanStatus> {
  const { data, error } = await supabase.rpc('kimi_plan_status')
  if (error) throw error
  return normalizeKimiPlanStatus(data)
}

// --- participants wall -------------------------------------------------------

export interface WallEntry {
  id: string; name: string; role: string | null; affiliation: string | null; city: string | null; blurb: string | null
  astro_level: number; ai_level: number; looking_for_team: boolean; team_name: string | null; joined_at: string
  github: string | null; seeking: string; seeking_count: number; avatar_url: string | null
}
export interface ParticipantsStats { total: number; on_wall: number; looking: number; teams: number }

export async function loadParticipantsWall(limit = 60): Promise<WallEntry[]> {
  const { data, error } = await supabase.rpc('participants_wall', { p_limit: limit })
  if (error) throw error
  return (data ?? []) as WallEntry[]
}

export async function loadParticipantsStats(): Promise<ParticipantsStats> {
  const { data, error } = await supabase.rpc('participants_stats')
  if (error) throw error
  return data as ParticipantsStats
}

export async function revealTeammateContact(id: string): Promise<{ contact: string; github: string } | null> {
  const { data, error } = await supabase.rpc('teammate_contact', { p_id: id })
  if (error) throw error
  return (data ?? null) as { contact: string; github: string } | null
}

// --- organizer quota reset notice --------------------------------------------

export async function loadQuotaResetNotice(): Promise<QuotaResetNotice | null> {
  const { data, error } = await supabase.rpc('observer_quota_reset_notice')
  if (error) throw error
  return normalizeQuotaResetNotice(data)
}

/** The 参赛 layout switch (site_settings 'event'.compete_ui); classic when unset or unreadable. */
export async function loadCompeteUiSetting(): Promise<CompeteUiSetting> {
  try {
    const { data } = await supabase.from('site_settings').select('value').eq('key', 'event').maybeSingle()
    return parseCompeteUi(data?.value)
  } catch { return parseCompeteUi(null) }
}
