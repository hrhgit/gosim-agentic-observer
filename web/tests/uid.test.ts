import assert from 'node:assert/strict'
import test from 'node:test'
import { boxesOverlap, isDoubleTap, uidLabel } from '../src/lib/uid.ts'

test('the label is shown only to a logged-in user with a valid 9-digit UID', () => {
  assert.equal(uidLabel(true, 100000123), 'UID 100000123')
  assert.equal(uidLabel(true, '100000001'), 'UID 100000001')
  assert.equal(uidLabel(false, 100000123), null)
  for (const bad of [null, undefined, 0, 99999999, 1000000000, 100000001.5, 'abc', '', {}]) assert.equal(uidLabel(true, bad), null)
})

test('overlap is checked with a margin, and hidden elements never count', () => {
  const label = { left: 1300, top: 870, right: 1430, bottom: 890 }
  assert.equal(boxesOverlap(label, { left: 0, top: 880, right: 390, bottom: 940 }), false)
  assert.equal(boxesOverlap(label, { left: 0, top: 880, right: 1440, bottom: 940 }), true)
  assert.equal(boxesOverlap(label, { left: 1400, top: 300, right: 1420, bottom: 600 }), false)
  assert.equal(boxesOverlap(label, { left: 1400, top: 300, right: 1420, bottom: 866 }, 6), true)
  assert.equal(boxesOverlap(label, { left: 0, top: 0, right: 0, bottom: 0 }), false)
})

test('a double tap is two taps close in time and place', () => {
  assert.equal(isDoubleTap(null, 100, 10, 10), false)
  assert.equal(isDoubleTap({ t: 100, x: 10, y: 10 }, 400, 12, 11), true)
  assert.equal(isDoubleTap({ t: 100, x: 10, y: 10 }, 500, 12, 11), false)
  assert.equal(isDoubleTap({ t: 100, x: 10, y: 10 }, 300, 80, 10), false)
})
