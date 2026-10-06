import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeMyQr, normalizeVisibleQrs, qrFileProblem, QR_MAX_BYTES } from '../src/lib/wechatQr.ts'

test('only PNG, JPEG or WebP up to 1 MB may be uploaded', () => {
  assert.equal(qrFileProblem({ size: 2000, type: 'image/png' }), null)
  assert.equal(qrFileProblem({ size: QR_MAX_BYTES, type: 'image/webp' }), null)
  assert.equal(qrFileProblem({ size: QR_MAX_BYTES + 1, type: 'image/jpeg' }), 'too_large')
  assert.equal(qrFileProblem({ size: 2000, type: 'image/gif' }), 'not_an_image')
  assert.equal(qrFileProblem({ size: 0, type: 'image/png' }), 'not_an_image')
  assert.equal(qrFileProblem(null), 'not_an_image')
})

test('payloads are normalized; visibility defaults to friends', () => {
  assert.equal(normalizeMyQr(null), null)
  assert.deepEqual(normalizeMyQr({ path: 'u/x.png', visibility: 'weird' }), { path: 'u/x.png', visibility: 'friends', updated_at: '' })
  assert.equal(normalizeMyQr({ path: 'u/x.png', visibility: 'all' })?.visibility, 'all')
  assert.deepEqual(normalizeVisibleQrs({ a: 'a/1.png', b: null, c: 3 }), { a: 'a/1.png' })
  assert.deepEqual(normalizeVisibleQrs(['x']), {})
})
