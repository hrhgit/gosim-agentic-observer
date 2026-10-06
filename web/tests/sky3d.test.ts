import assert from 'node:assert/strict'
import test from 'node:test'
import { applyMatrix, daylight, equatorialVec, fieldOffset, horizonMatrix, moonIllumination, separationDeg, sunRaDec } from '../src/lib/sky3d.ts'

const DEG = Math.PI / 180
const site = { lat: -24.6157, lon: -70.3976 }
function lst(unix: number) {
  const d = 2440587.5 + unix / 86400 - 2451545.0
  return (((280.46061837 + 360.98564736629 * d + site.lon) % 360) + 360) % 360
}
function altitude(ra: number, dec: number, unix: number) {
  const h = (lst(unix) - ra) * DEG
  return Math.asin(Math.sin(site.lat * DEG) * Math.sin(dec * DEG) + Math.cos(site.lat * DEG) * Math.cos(dec * DEG) * Math.cos(h)) / DEG
}
const local = (ra: number, dec: number, unix: number) => applyMatrix(horizonMatrix(site.lat, lst(unix)), equatorialVec(ra, dec))

test('the local frame puts a star at the altitude the scorer computes', () => {
  const t = Date.parse('2026-11-03T03:00:00Z') / 1000
  for (const [ra, dec] of [[12.7, -32], [99.2, -19.8], [300, -60], [180, 10]] as const) {
    const v = local(ra, dec, t)
    assert.ok(Math.abs(Math.asin(v.y) / DEG - altitude(ra, dec, t)) < 1e-6)
    assert.ok(Math.abs(Math.hypot(v.x, v.y, v.z) - 1) < 1e-9)
  }
})

test('the zenith is straight up and a rising star is in the east', () => {
  const t = Date.parse('2026-11-03T03:00:00Z') / 1000
  const z = local(lst(t), site.lat, t)
  assert.ok(z.y > 0.999999)
  const east = local(lst(t) + 90, 0, t)
  assert.ok(east.x > 0.999)
})

test('the sun is down during the replay nights and up at local noon', () => {
  const night = Date.parse('2026-11-03T04:00:00Z') / 1000
  const noon = Date.parse('2026-11-03T16:40:00Z') / 1000
  const sunAlt = (t: number) => { const s = sunRaDec(t); return altitude(s.ra, s.dec, t) }
  assert.ok(sunAlt(night) < -18)
  assert.ok(sunAlt(noon) > 60)
  assert.equal(daylight(sunAlt(night)), 0)
  assert.equal(daylight(sunAlt(noon)), 1)
})

test('the moon wanes towards new moon across the first week of November 2026', () => {
  const a = moonIllumination(Date.parse('2026-11-02T04:00:00Z') / 1000)
  const b = moonIllumination(Date.parse('2026-11-08T04:00:00Z') / 1000)
  assert.ok(a > b && b < 0.1 && a > 0.35)
})

test('field offsets agree with angular separation near the centre', () => {
  const o = fieldOffset(12.687, -32, 13.5, -31.2)
  assert.ok(Math.abs(Math.hypot(o.e, o.n) - separationDeg(12.687, -32, 13.5, -31.2)) < 0.01)
  assert.ok(o.e > 0 && o.n > 0)
})
