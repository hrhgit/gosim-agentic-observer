/** How a team's model key reaches formal runs. */
export type ModelKeyMode = 'stored' | 'relay'

/**
 * Saving the key encrypted on the server is the default: with the page relay the
 * page must stay open for every evaluation, which contestants keep forgetting. A
 * team that explicitly chose relay (or the server has not resolved a mode yet)
 * stays in relay mode.
 */
export const DEFAULT_MODEL_KEY_MODE: ModelKeyMode = 'stored'

export function teamModelMode(teamModel: { mode?: string | null } | null | undefined): ModelKeyMode {
  return teamModel?.mode === 'relay' ? 'relay' : teamModel?.mode === 'stored' ? 'stored' : DEFAULT_MODEL_KEY_MODE
}

/** Which shape the team's provider speaks: OpenAI-compatible /v1/chat/completions
 *  (default) or the Anthropic Messages API (/v1/messages). The platform never
 *  translates between them; a mismatched call is refused. */
export type ModelProtocol = 'openai' | 'anthropic'
export const DEFAULT_MODEL_PROTOCOL: ModelProtocol = 'openai'

export function teamModelProtocol(teamModel: { protocol?: string | null } | null | undefined): ModelProtocol {
  return teamModel?.protocol === 'anthropic' ? 'anthropic' : DEFAULT_MODEL_PROTOCOL
}

/**
 * The hidden final (organizers run each team's final version once after the
 * online phase freezes) has no team page open, so a team in relay mode cannot
 * make model calls there. Rule 2026-09-27: switch to stored mode before the
 * online phase ends. Warn while a final version applies and the team relays.
 */
export function relayMissesHiddenFinal(mode: ModelKeyMode, finalApplies: boolean): boolean {
  return finalApplies && mode === 'relay'
}
