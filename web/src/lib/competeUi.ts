// Which layout the 参赛 workspace uses. The simplified layout ("v2") is rolled out behind a
// switch stored in site_settings row key 'event' (already publicly readable, otherwise unused):
//   { "compete_ui": { "v2_all": false, "v2_teams": ["<team uuid>", ...] } }
// Missing row or field = classic layout. A person can force a layout for themselves with
// ?ui=v2 / ?ui=v1 (remembered in this browser; ?ui=auto forgets it).
export type CompeteUiSetting = { v2_all: boolean; v2_teams: string[] }
export type CompeteLayout = 'classic' | 'v2'
export const COMPETE_UI_STORAGE = 'compete-ui-layout'

export function parseCompeteUi(eventValue: unknown): CompeteUiSetting {
  const raw = eventValue && typeof eventValue === 'object' ? (eventValue as Record<string, unknown>).compete_ui : null
  const ui = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
  return {
    v2_all: ui.v2_all === true,
    v2_teams: Array.isArray(ui.v2_teams) ? ui.v2_teams.filter((t): t is string => typeof t === 'string') : [],
  }
}

/** The person's own choice from ?ui=…, falling back to the remembered one; null means "follow the switch". */
export function layoutOverride(query: string | null, remembered: string | null): CompeteLayout | null {
  const pick = (v: string | null) => v === 'v2' ? 'v2' : v === 'v1' || v === 'classic' ? 'classic' : null
  if (query === 'auto') return null
  return pick(query) ?? pick(remembered)
}

export function competeLayout(setting: CompeteUiSetting, teamId: string | null | undefined, override: CompeteLayout | null): CompeteLayout {
  if (override) return override
  return setting.v2_all || (!!teamId && setting.v2_teams.includes(teamId)) ? 'v2' : 'classic'
}
