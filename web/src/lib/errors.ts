import type { I18n } from '../composables/useI18n'

/** Map a Supabase / PostgREST error to a user-facing string via the given i18n namespaces. */
export function describeError(error: unknown, i18n: I18n, namespaces: string[] = []): string {
  const message = extractMessage(error)
  if (!message) return i18n.t('errors.generic')
  const code = message.trim()
  // The page's own wording first, then the shared list, so no page ever shows a bare backend code.
  for (const ns of [...namespaces, 'errors']) {
    const translated = i18n.t(`${ns}.${code}`)
    if (translated !== `${ns}.${code}`) return translated
  }
  const auth = AUTH_MESSAGES[code]
  if (auth) return i18n.t(auth)
  if (/Failed to fetch|NetworkError|Load failed/i.test(code)) return i18n.t('errors.generic')
  return code
}

export function extractMessage(error: unknown): string {
  if (!error) return ''
  if (typeof error === 'string') return error
  if (typeof error === 'object') {
    const e = error as { message?: unknown; error_description?: unknown; details?: unknown }
    if (typeof e.message === 'string') return e.message
    if (typeof e.error_description === 'string') return e.error_description
    if (typeof e.details === 'string') return e.details
  }
  return String(error)
}

const AUTH_MESSAGES: Record<string, string> = {
  'Invalid login credentials': 'auth.errors.bad_credentials',
  'User already registered': 'auth.errors.email_taken',
  'Password should be at least 8 characters.': 'auth.errors.password_too_short',
  'Password should be at least 6 characters.': 'auth.errors.password_too_short',
  'Email rate limit exceeded': 'auth.errors.rate_limited',
  'Request rate limit reached': 'auth.errors.rate_limited',
  'Unable to validate email address: invalid format': 'auth.errors.email_invalid',
  'Signups not allowed for this instance': 'auth.errors.registration_closed',
  'registration_closed': 'auth.errors.registration_closed',
  'banned': 'auth.errors.banned',
  'not_authenticated': 'errors.session_required',
  'admin_only': 'errors.admin_required',
}
