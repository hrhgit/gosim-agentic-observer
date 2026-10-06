import assert from 'node:assert/strict'
import test from 'node:test'
import { StorageApiError } from '@supabase/supabase-js'
import { isAlreadyUploaded, isDuplicateUploadResponse } from '../src/lib/uploadConflict.ts'

// Every upload id gets its own fresh storage path (observer_reserve_upload), so a
// "duplicate" conflict on a retried PUT can only mean our own earlier attempt already
// landed despite the client never seeing its response — the retry should read as success.
test('re-uploading the same upload id after a dropped response reads as success, not failure', () => {
  assert.equal(isAlreadyUploaded(new StorageApiError('The resource already exists', 400, '409', 'storage', 'Duplicate')), true)
  assert.equal(isAlreadyUploaded(new StorageApiError('Duplicate', 409, '409', 'storage')), true)
})

test('a genuine storage failure is not mistaken for a successful retry', () => {
  assert.equal(isAlreadyUploaded(new StorageApiError('The object exceeded the maximum allowed size', 413, '413', 'storage')), false)
  assert.equal(isAlreadyUploaded(new Error('network down')), false)
})

test('the hand-rolled XHR upload path recognizes the same conflict from raw response text', () => {
  assert.equal(isDuplicateUploadResponse(400, '{"statusCode":"409","error":"Duplicate","message":"The resource already exists"}'), true)
  assert.equal(isDuplicateUploadResponse(413, 'Payload too large'), false)
  assert.equal(isDuplicateUploadResponse(0, ''), false)
})
