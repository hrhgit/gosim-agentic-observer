import assert from 'node:assert/strict'
import test from 'node:test'
import { EVENT_TIME_ZONE, startDay, startMoment, timeLeft, upcomingPublicPhases, type ScheduledPhaseRow } from '../src/lib/phaseSchedule.ts'

const now = Date.parse('2026-09-25T09:30:00Z')
const row = (slug: string, starts_at: string | null, over: Partial<ScheduledPhaseRow> = {}): ScheduledPhaseRow => ({
  id: slug, slug, name_en: slug, name_zh: slug, sort_order: 0, starts_at, ends_at: null, is_active: true, ...over,
})

test('the next public stage is the soonest active phase that has not started', () => {
  const rows = [
    row('final', '2026-10-17T01:00:00Z'),
    row('online', '2026-10-04T16:00:00Z', { observer_settings: { access_team_id: null } }),
    row('practice', '2026-09-10T00:00:00Z'),
    row('undated', null),
    row('retired', '2026-10-01T00:00:00Z', { is_active: false }),
  ]
  assert.deepEqual(upcomingPublicPhases(rows, now).map(p => p.slug), ['online', 'final'])
  assert.ok(!('observer_settings' in upcomingPublicPhases(rows, now)[0]!))
})

test('team-restricted test phases are never the public next stage', () => {
  const rows = [
    row('beta-acceptance', '2026-09-28T00:00:00Z', { observer_settings: { access_team_id: 'team-1' } }),
    row('beta-array', '2026-09-29T00:00:00Z', { observer_settings: [{ access_team_id: 'team-1' }] }),
    row('online', '2026-10-04T16:00:00Z', { observer_settings: [] }),
  ]
  assert.deepEqual(upcomingPublicPhases(rows, now).map(p => p.slug), ['online'])
})

test('the sealed hidden final phase is never shown as the next stage', () => {
  const rows = [
    row('final-hidden', '2026-10-07T15:59:00Z', { observer_settings: { access_team_id: null, sealed: true } }),
    row('online', '2026-10-04T16:00:00Z', { observer_settings: [{ access_team_id: null, sealed: false }] }),
  ]
  assert.deepEqual(upcomingPublicPhases(rows, now).map(p => p.slug), ['online'])
})

test('time left reads in whole days, then hours', () => {
  assert.deepEqual(timeLeft('2026-10-04T16:00:00Z', now), { unit: 'days', n: 9 })
  assert.deepEqual(timeLeft('2026-09-26T12:00:00Z', now), { unit: 'day', n: 1 })
  assert.deepEqual(timeLeft('2026-09-27T10:00:00Z', now), { unit: 'days', n: 2 })
  assert.deepEqual(timeLeft('2026-09-25T15:45:00Z', now), { unit: 'hours', n: 6 })
  assert.deepEqual(timeLeft('2026-09-25T10:00:00Z', now), { unit: 'soon', n: 0 })
})

test('the start day is shown in the viewer time zone and language', () => {
  const start = '2026-10-04T16:00:00Z'
  assert.equal(startDay(start, 'zh', 'Asia/Shanghai'), '10月5日')
  assert.equal(startDay(start, 'en', 'Asia/Shanghai'), 'October 5')
  assert.equal(startDay(start, 'en', 'Europe/Paris'), 'October 4')
  assert.equal(startDay(start, 'fr', 'Asia/Shanghai'), '5 octobre')
  assert.equal(startDay(start, 'ja', 'Asia/Tokyo'), '10月5日')
  assert.match(startMoment(start, 'zh', 'Asia/Shanghai'), /2026年10月5日.*00:00/)
  // The site announces the online competition as October 5–7, Beijing time.
  assert.equal(startDay(start, 'en', EVENT_TIME_ZONE), 'October 5')
})
