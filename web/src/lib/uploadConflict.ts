import { StorageApiError } from '@supabase/supabase-js'

/** Matches the storage server's wording when `upsert: false` refuses an existing object. */
const DUPLICATE_UPLOAD = /duplicate|already exists/i

// Each upload id gets a fresh, unique storage path (see observer_reserve_upload), so the
// only way a "duplicate" conflict can happen on that path is our own earlier attempt
// having already succeeded despite the client never seeing its response. Treat that as
// success instead of failure.
export function isAlreadyUploaded(error: unknown): boolean {
  return error instanceof StorageApiError && (DUPLICATE_UPLOAD.test(error.message) || error.statusCode === '409')
}

/** Same check for the hand-rolled XHR upload path, which only has the raw response text. */
export function isDuplicateUploadResponse(status: number, responseText: string): boolean {
  return status >= 400 && status < 500 && DUPLICATE_UPLOAD.test(responseText)
}
