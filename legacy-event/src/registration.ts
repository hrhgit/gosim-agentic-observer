// Public participant platform. Auth, teams and submissions stay on its Supabase project.
export const platformUrl = String(import.meta.env.VITE_AGENT_OBSERVER_URL || 'https://bh3gei.github.io/agent-observer').replace(/\/+$/, '')
export const registrationUrl = `${platformUrl}/register`
export const platformLinks = {
  login: `${platformUrl}/register?mode=login`,
  team: `${platformUrl}/team`,
  submit: `${platformUrl}/submit`,
  rules: `${platformUrl}/rules`,
  docs: `${platformUrl}/docs`,
  resources: `${platformUrl}/resources`,
  announcements: `${platformUrl}/announcements`,
  leaderboard: `${platformUrl}/leaderboard/online`,
}
// Anonymous, CORS-enabled Edge Function; no database credentials are needed here.
export const leaderboardApi = String(import.meta.env.VITE_AGENT_OBSERVER_LEADERBOARD_API || 'https://vdiemcofukuxglqsmlyz.supabase.co/functions/v1/leaderboard?phase=online')
