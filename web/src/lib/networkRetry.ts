/** Delays (ms) before each retry of a failure that never reached the server. */
export const RETRY_DELAYS_MS = [1000, 3000, 6000]

/**
 * Retries `fn` while `isNetworkError` recognizes its failure as a dropped connection
 * (no response reached the caller), not a response the server actually sent. Any other
 * failure, or running out of retries, rethrows immediately. `onRetry` fires before each
 * wait so callers can surface retry progress.
 */
export async function withNetworkRetry<T>(
  fn: () => Promise<T>,
  isNetworkError: (error: unknown) => boolean,
  onRetry?: (attempt: number) => void,
  delaysMs: readonly number[] = RETRY_DELAYS_MS,
): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await fn()
    } catch (error) {
      if (attempt >= delaysMs.length || !isNetworkError(error)) throw error
      onRetry?.(attempt + 1)
      await new Promise(resolve => setTimeout(resolve, delaysMs[attempt]))
    }
  }
}
