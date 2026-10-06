/** Only these three phases are ever shown to contestants, in this order — any other phase
 * (internal rehearsal/staging/observer boards) is filtered out even if the DB returns it. */
export const LEADERBOARD_SLUGS = ['practice-projects', 'practice', 'online'] as const

/** The leaderboard page also has the hidden final's tab, after the others. The database returns that phase
 * to participants only once the organizers publish it (observer_phase_visible / leaderboard_mode), so until
 * then the tab does not exist for them; organizers see it with a preview note. The home mini board keeps
 * LEADERBOARD_SLUGS. */
export const LEADERBOARD_PAGE_SLUGS = [...LEADERBOARD_SLUGS, 'final-hidden'] as const

export const LEADERBOARD_TAB_LABEL_KEYS: Record<string, string> = {
  'practice-projects': 'leaderboard.tabs.practice',
  practice: 'leaderboard.tabs.debug',
  online: 'leaderboard.tabs.competition',
  'final-hidden': 'leaderboard.tabs.final',
}
