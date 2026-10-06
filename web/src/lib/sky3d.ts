/**
 * Pure math for the 3D replay sky: where a star, the Sun and the Moon sit in an observatory's local
 * sky at a given moment. Kept free of any `three` import so it never drags the heavy module in ahead
 * of the lazy `import('three')` in ReplaySky3D.vue, and so node tests can check it.
 *
 * The scene is drawn in the observatory's own frame: y is straight up (the zenith), x points east and
 * -z points north, with the ground at y = 0. A star's place in that frame is its fixed equatorial
 * direction turned by one rotation that depends only on the site latitude and local sidereal time —
 * so the whole catalogue is placed once and the sky is turned with a single matrix each frame.
 * Local sidereal time comes in as an argument (lstDeg in skymap.ts) so this file imports nothing.
 */

export interface Vec3 { x: number; y: number; z: number }
const DEG = Math.PI / 180
const mod = (a: number, n: number) => ((a % n) + n) % n

/** Fixed equatorial unit vector for a RA/Dec (degrees): x toward RA 0, y toward RA 90°, z toward the north pole. */
export function equatorialVec(ra: number, dec: number): Vec3 {
  const c = Math.cos(dec * DEG)
  return { x: c * Math.cos(ra * DEG), y: c * Math.sin(ra * DEG), z: Math.sin(dec * DEG) }
}

/**
 * Row-major 3x3 rotation taking an equatorial vector into the local frame (x east, y up, z south) of
 * a site at latitude latDeg when the local sidereal time is lstDegrees. A proper rotation, so it can
 * be dropped straight into a Matrix4.
 */
export function horizonMatrix(latDeg: number, lstDegrees: number): number[] {
  const l = lstDegrees * DEG
  const p = latDeg * DEG
  const cl = Math.cos(l), sl = Math.sin(l), cp = Math.cos(p), sp = Math.sin(p)
  return [
    -sl, cl, 0,
    cp * cl, cp * sl, sp,
    sp * cl, sp * sl, -cp,
  ]
}

export function applyMatrix(m: number[], v: Vec3): Vec3 {
  return {
    x: m[0]! * v.x + m[1]! * v.y + m[2]! * v.z,
    y: m[3]! * v.x + m[4]! * v.y + m[5]! * v.z,
    z: m[6]! * v.x + m[7]! * v.y + m[8]! * v.z,
  }
}

/** Low-precision Sun RA/Dec (the Astronomical Almanac's short formula, good to about 0.01°). */
export function sunRaDec(unixSeconds: number): { ra: number; dec: number } {
  const n = unixSeconds / 86400 - 10957.5
  const L = mod(280.46 + 0.9856474 * n, 360)
  const g = mod(357.528 + 0.9856003 * n, 360) * DEG
  const lambda = (L + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * DEG
  const eps = (23.439 - 0.0000004 * n) * DEG
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lambda), Math.cos(lambda))
  const dec = Math.asin(Math.sin(eps) * Math.sin(lambda))
  return { ra: mod(ra / DEG, 360), dec: dec / DEG }
}

/**
 * Low-precision Moon RA/Dec (truncated Meeus ch. 47 — leading terms only, good to a degree or two).
 * Plenty for a marker in the sky; this view isn't an almanac.
 */
export function moonRaDec(unixSeconds: number): { ra: number; dec: number } {
  const d = unixSeconds / 86400 - 10957.5
  const L = mod(218.316 + 13.176396 * d, 360) * DEG
  const M = mod(134.963 + 13.064993 * d, 360) * DEG
  const F = mod(93.272 + 13.22935 * d, 360) * DEG
  const lon = L + 6.289 * DEG * Math.sin(M)
  const lat = 5.128 * DEG * Math.sin(F)
  const obliq = 23.4393 * DEG
  const sinLat = Math.sin(lat), cosLat = Math.cos(lat)
  const sinLon = Math.sin(lon), cosLon = Math.cos(lon)
  const dec = Math.asin(sinLat * Math.cos(obliq) + cosLat * Math.sin(obliq) * sinLon)
  const ra = Math.atan2(sinLon * Math.cos(obliq) - (sinLat / cosLat) * Math.sin(obliq), cosLon)
  return { ra: mod(ra / DEG, 360), dec: dec / DEG }
}

/** Fraction of the Moon's disc that is lit, from its angle to the Sun (0 new, 1 full). */
export function moonIllumination(unixSeconds: number): number {
  const s = sunRaDec(unixSeconds), m = moonRaDec(unixSeconds)
  const a = equatorialVec(s.ra, s.dec), b = equatorialVec(m.ra, m.dec)
  const cosElong = Math.max(-1, Math.min(1, a.x * b.x + a.y * b.y + a.z * b.z))
  return (1 - cosElong) / 2
}

/** Angular separation of two RA/Dec points, degrees. */
export function separationDeg(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const a = equatorialVec(ra1, dec1), b = equatorialVec(ra2, dec2)
  return Math.acos(Math.max(-1, Math.min(1, a.x * b.x + a.y * b.y + a.z * b.z))) / DEG
}

/**
 * Gnomonic (tangent-plane) offsets of a point from a field centre, degrees: +east, +north — how the
 * point sits in the telescope's field of view.
 */
export function fieldOffset(ra0: number, dec0: number, ra: number, dec: number): { e: number; n: number } {
  const d0 = dec0 * DEG, d = dec * DEG, da = (ra - ra0) * DEG
  const cosc = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(da)
  const k = 1 / Math.max(1e-6, cosc) / DEG
  return {
    e: k * Math.cos(d) * Math.sin(da),
    n: k * (Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(da)),
  }
}

/** How much daylight is in the sky for a Sun altitude: 0 at astronomical night, 1 in full day. */
export function daylight(sunAltDeg: number): number {
  const x = Math.max(0, Math.min(1, (sunAltDeg + 14) / 20))
  return x * x * (3 - 2 * x)
}

/** Whether a WebGL context is actually obtainable — the 3D view's gate before it loads `three`. */
export function supportsWebGL(): boolean {
  if (typeof document === 'undefined') return false
  try {
    const canvas = document.createElement('canvas')
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl')
    // Hand the probe context straight back: browsers cap live contexts, and the scene needs its own.
    gl?.getExtension('WEBGL_lose_context')?.loseContext()
    return !!gl
  } catch {
    return false
  }
}
