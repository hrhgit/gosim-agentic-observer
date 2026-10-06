/**
 * Sky geometry for the agent: the same formulas the engine uses, so planned hits are real hits.
 *
 * All angles in degrees. Azimuth: 0 = north, 90 = east. Times are UTC instants in epoch seconds.
 * Port of python-pro/skymath.py; the small helpers at the top reproduce Python's float semantics
 * (`%`, `//`, `round`, `sum`) so both agents take the same decisions.
 */

export const SIDEREAL_DEG_PER_SECOND = 360.98564736629 / 86400.0;

// --- Python float semantics ---------------------------------------------------------------------

const DEG_TO_RAD = Math.PI / 180.0;
const RAD_TO_DEG = 180.0 / Math.PI;
export const radians = (deg: number): number => deg * DEG_TO_RAD;
export const degrees = (rad: number): number => rad * RAD_TO_DEG;

/** Python's `a % b` for floats: the result takes the sign of b. */
export function pymod(a: number, b: number): number {
  let mod = a % b;
  if (mod !== 0) {
    if (b < 0 !== mod < 0) mod += b;
  } else {
    mod = b < 0 ? -0 : 0;
  }
  return mod;
}

/** Python's `a // b` for floats (CPython float_floor_div). */
export function floordiv(a: number, b: number): number {
  let mod = a % b;
  let div = (a - mod) / b;
  if (mod !== 0 && b < 0 !== mod < 0) {
    mod += b;
    div -= 1.0;
  }
  if (div === 0) return b < 0 !== a < 0 ? -0 : 0;
  let floored = Math.floor(div);
  if (div - floored > 0.5) floored += 1.0;
  return floored;
}

/** Python's `round(x, digits)` (exact decimal value of the double, as CPython rounds it). */
export function pyround(x: number, digits: number): number {
  if (!Number.isFinite(x)) return x;
  const fixed = x.toFixed(digits);
  // toFixed breaks exact decimal ties away from zero, Python to even; exact ties are vanishingly rare here
  return Number(fixed);
}

/** Python's built-in `sum()` over floats (3.12+: Neumaier compensated summation). */
export function pysum(values: Iterable<number>): number {
  let f = 0.0;
  let c = 0.0;
  let first = true;
  for (const x of values) {
    if (first) {
      f = 0 + x;
      first = false;
      continue;
    }
    const t = f + x;
    if (Math.abs(f) >= Math.abs(x)) c += f - t + x;
    else c += x - t + f;
    f = t;
  }
  if (c && Number.isFinite(c)) f += c;
  return f;
}

/** `sorted(values)[len(values) // 2]` */
export function upperMedian(values: number[]): number {
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.floor(ordered.length / 2)] as number;
}

// --- time ------------------------------------------------------------------------------------------

export function parseUtc(value: string): number {
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) throw new Error(`bad UTC timestamp ${value}`);
  return ms / 1000.0;
}

export function formatUtc(moment: number): string {
  return new Date(Math.floor(moment) * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function julianDate(moment: number): number {
  return moment / 86400.0 + 2440587.5;
}

export function localSiderealDeg(moment: number, longitudeDeg: number): number {
  const days = julianDate(moment) - 2451545.0;
  return pymod(280.46061837 + 360.98564736629 * days + longitudeDeg, 360.0);
}

export function wrap180(angle: number): number {
  return pymod(angle + 180.0, 360.0) - 180.0;
}

// --- coordinates ------------------------------------------------------------------------------------

/** Equatorial -> horizontal for a given local sidereal time. Returns [alt, az]. */
export function radecToAltaz(raDeg: number, decDeg: number, lstDeg: number, latitudeDeg: number): [number, number] {
  const hourAngle = radians(wrap180(lstDeg - raDeg));
  const lat = radians(latitudeDeg);
  const dec = radians(decDeg);
  const sinAlt = Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(hourAngle);
  const alt = Math.asin(Math.max(-1.0, Math.min(1.0, sinAlt)));
  const cosAlt = Math.max(1e-12, Math.cos(alt));
  const sinAz = (-Math.sin(hourAngle) * Math.cos(dec)) / cosAlt;
  const cosAz = (Math.sin(dec) - Math.sin(alt) * Math.sin(lat)) / (cosAlt * Math.max(1e-12, Math.cos(lat)));
  return [degrees(alt), pymod(degrees(Math.atan2(sinAz, cosAz)), 360.0)];
}

export function altazToRadec(altDeg: number, azDeg: number, lstDeg: number, latitudeDeg: number): [number, number] {
  const alt = radians(altDeg);
  const az = radians(azDeg);
  const lat = radians(latitudeDeg);
  const sinDec = Math.sin(alt) * Math.sin(lat) + Math.cos(alt) * Math.cos(lat) * Math.cos(az);
  const dec = Math.asin(Math.max(-1.0, Math.min(1.0, sinDec)));
  const cosDec = Math.max(1e-12, Math.cos(dec));
  const sinH = (-Math.sin(az) * Math.cos(alt)) / cosDec;
  const cosH = (Math.sin(alt) - Math.sin(dec) * Math.sin(lat)) / (cosDec * Math.max(1e-12, Math.cos(lat)));
  const hour = degrees(Math.atan2(sinH, cosH));
  return [pymod(lstDeg - hour, 360.0), degrees(dec)];
}

/** Largest |hour angle| at which a source stays at or above minAlt (0 = never, 180 = always). */
export function maxHourAngleDeg(decDeg: number, latitudeDeg: number, minAltDeg: number): number {
  const lat = radians(latitudeDeg);
  const dec = radians(decDeg);
  const denominator = Math.cos(lat) * Math.cos(dec);
  if (Math.abs(denominator) < 1e-12) return 0.0;
  const value = (Math.sin(radians(minAltDeg)) - Math.sin(lat) * Math.sin(dec)) / denominator;
  if (value >= 1.0) return 0.0;
  if (value <= -1.0) return 180.0;
  return degrees(Math.acos(value));
}

/** [north, east] gnomonic offsets in degrees of a target from a field centre, or null. */
export function tangentOffsets(targetAlt: number, targetAz: number, centerAlt: number, centerAz: number): [number, number] | null {
  const alt = radians(targetAlt);
  const az = radians(targetAz);
  const calt = radians(centerAlt);
  const caz = radians(centerAz);
  const t0 = Math.cos(alt) * Math.cos(az);
  const t1 = Math.cos(alt) * Math.sin(az);
  const t2 = Math.sin(alt);
  const c0 = Math.cos(calt) * Math.cos(caz);
  const c1 = Math.cos(calt) * Math.sin(caz);
  const c2 = Math.sin(calt);
  const n0 = -Math.sin(calt) * Math.cos(caz);
  const n1 = -Math.sin(calt) * Math.sin(caz);
  const n2 = Math.cos(calt);
  const e0 = -Math.sin(caz);
  const e1 = Math.cos(caz);
  const e2 = 0.0;
  const depth = t0 * c0 + t1 * c1 + t2 * c2;
  if (depth <= 0.0) return null;
  return [degrees((t0 * n0 + t1 * n1 + t2 * n2) / depth), degrees((t0 * e0 + t1 * e1 + t2 * e2) / depth)];
}

/** The direction dNorth / dEast degrees away on the tangent plane at (alt, az). Works near the zenith. */
export function shiftAltaz(altDeg: number, azDeg: number, dNorth: number, dEast: number): [number, number] {
  const alt = radians(altDeg);
  const az = radians(azDeg);
  const point = [Math.cos(alt) * Math.cos(az), Math.cos(alt) * Math.sin(az), Math.sin(alt)];
  const north = [-Math.sin(alt) * Math.cos(az), -Math.sin(alt) * Math.sin(az), Math.cos(alt)];
  const east = [-Math.sin(az), Math.cos(az), 0.0];
  const dn = radians(dNorth);
  const de = radians(dEast);
  let [x, y, z] = [0, 1, 2].map((k) => (point[k] as number) + dn * (north[k] as number) + de * (east[k] as number)) as [number, number, number];
  const norm = Math.sqrt(x * x + y * y + z * z);
  x /= norm;
  y /= norm;
  z /= norm;
  return [degrees(Math.asin(Math.max(-1.0, Math.min(1.0, z)))), pymod(degrees(Math.atan2(y, x)), 360.0)];
}

export interface Instrument {
  grid_side: number;
  n_fibers: number;
  glass_side_deg: number;
  pitch_deg: number;
  fov_side_deg: number;
  exposure: { min_duration_seconds: number; max_duration_seconds: number };
}

/** n x n square fibres; fibre 0 bottom-left, rows along +alt, columns along +az. */
export class FiberGrid {
  readonly side: number;
  readonly n: number;
  readonly glass: number;
  readonly pitch: number;
  readonly fov: number;

  constructor(instrument: Instrument) {
    this.side = Math.trunc(Number(instrument.grid_side));
    this.n = Math.trunc(Number(instrument.n_fibers));
    this.glass = Number(instrument.glass_side_deg);
    this.pitch = Number(instrument.pitch_deg);
    this.fov = Number(instrument.fov_side_deg);
  }

  fiberCenter(fiber: number): [number, number] {
    const row = Math.floor(fiber / this.side);
    const col = fiber - row * this.side;
    const middle = (this.side - 1) / 2.0;
    return [(row - middle) * this.pitch, (col - middle) * this.pitch];
  }

  /** -> [fiber id, margin in degrees to the glass edge] or [null, negative] when not on glass. */
  classify(dNorth: number, dEast: number): [number | null, number] {
    const half = this.fov / 2.0;
    if (Math.abs(dNorth) > half || Math.abs(dEast) > half) return [null, -1.0];
    const middle = this.side / 2.0;
    const row = Math.min(Math.max(Math.floor(dNorth / this.pitch + middle), 0), this.side - 1);
    const col = Math.min(Math.max(Math.floor(dEast / this.pitch + middle), 0), this.side - 1);
    const fiber = row * this.side + col;
    const [cNorth, cEast] = this.fiberCenter(fiber);
    const margin = this.glass / 2.0 - Math.max(Math.abs(dNorth - cNorth), Math.abs(dEast - cEast));
    return margin >= 0.0 ? [fiber, margin] : [null, margin];
  }
}

const AIRMASS_ZENITH = 1.0 / (1.0 + 0.50572 * 96.07995 ** -1.6364);

export function normalizedAirmass(altDeg: number): number {
  if (altDeg <= 0.0) return Infinity;
  const zenith = 90.0 - altDeg;
  const raw = 1.0 / (Math.cos(radians(zenith)) + 0.50572 * (96.07995 - zenith) ** -1.6364);
  return raw / AIRMASS_ZENITH;
}

export function sunRadec(moment: number): [number, number] {
  const days = julianDate(moment) - 2451545.0;
  const meanLongitude = pymod(280.46 + 0.9856474 * days, 360.0);
  const anomaly = radians(pymod(357.528 + 0.9856003 * days, 360.0));
  const longitude = radians(pymod(meanLongitude + 1.915 * Math.sin(anomaly) + 0.02 * Math.sin(2 * anomaly), 360.0));
  const obliquity = radians(23.439 - 0.0000004 * days);
  return [
    pymod(degrees(Math.atan2(Math.cos(obliquity) * Math.sin(longitude), Math.cos(longitude))), 360.0),
    degrees(Math.asin(Math.sin(obliquity) * Math.sin(longitude))),
  ];
}

export function moonRadec(moment: number): [number, number] {
  const days = julianDate(moment) - 2451545.0;
  const meanLongitude = radians(pymod(218.316 + 13.176396 * days, 360.0));
  const anomaly = radians(pymod(134.963 + 13.064993 * days, 360.0));
  const argLatitude = radians(pymod(93.272 + 13.22935 * days, 360.0));
  const longitude = meanLongitude + radians(6.289) * Math.sin(anomaly);
  const latitude = radians(5.128) * Math.sin(argLatitude);
  const obliquity = radians(23.439 - 0.0000004 * days);
  const x = Math.cos(longitude) * Math.cos(latitude);
  const y = Math.sin(longitude) * Math.cos(latitude) * Math.cos(obliquity) - Math.sin(latitude) * Math.sin(obliquity);
  const z = Math.sin(longitude) * Math.cos(latitude) * Math.sin(obliquity) + Math.sin(latitude) * Math.cos(obliquity);
  return [pymod(degrees(Math.atan2(y, x)), 360.0), degrees(Math.asin(z))];
}

export function separationDeg(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const r1 = radians(ra1);
  const d1 = radians(dec1);
  const r2 = radians(ra2);
  const d2 = radians(dec2);
  const cosine = Math.sin(d1) * Math.sin(d2) + Math.cos(d1) * Math.cos(d2) * Math.cos(r1 - r2);
  return degrees(Math.acos(Math.max(-1.0, Math.min(1.0, cosine))));
}

export interface LunarModel {
  maximum_penalty: number;
  altitude_exponent: number;
  angular_decay_scale_deg: number;
}

/** Moon state at one instant; lunarFactor() is the public lunar model from the score config. */
export class Moon {
  readonly ra: number;
  readonly dec: number;
  readonly illumination: number;
  readonly alt: number;

  constructor(moment: number, lstDeg: number, latitudeDeg: number, private readonly model: LunarModel) {
    [this.ra, this.dec] = moonRadec(moment);
    const [sunRa, sunDec] = sunRadec(moment);
    this.illumination = (1.0 - Math.cos(radians(separationDeg(sunRa, sunDec, this.ra, this.dec)))) / 2.0;
    this.alt = radecToAltaz(this.ra, this.dec, lstDeg, latitudeDeg)[0];
  }

  lunarFactor(raDeg: number, decDeg: number): number {
    if (this.alt <= 0.0) return 1.0;
    const separation = separationDeg(raDeg, decDeg, this.ra, this.dec);
    const penalty =
      Number(this.model.maximum_penalty) *
      this.illumination *
      Math.sin(radians(this.alt)) ** Number(this.model.altitude_exponent) *
      Math.exp(-separation / Number(this.model.angular_decay_scale_deg));
    return Math.max(0.0, Math.min(1.0, 1.0 - penalty));
  }
}
