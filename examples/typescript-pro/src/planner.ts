/**
 * Planner of the pro agent: pointing, fibre assignment, duration and program in one search.
 *
 * Per decision (plan):
 * 1. Visible targets with something left to gain are ranked with a cheap proxy; the best POOL go on.
 * 2. The gain of one exposure of duration T for target i is
 *        weight * (reach(T) * program multiplier - best so far)
 *      + REQUIRED_BONUS * P(required target reaches factor 0.5)   (discounted while its sky is far from its best)
 *      + request bonus  * P(request target reaches its threshold)
 *    times an urgency factor for targets with few nights left.
 * 3. Candidate fields: the N_ANCHORS best targets centred on each of the 16 fibres, plus the N_DENSE densest
 *    patches of remaining science. Every fibre gets the target with the largest gain there, for each
 *    duration; the winner maximises  total gain - lambda * T  (lambda = time price, scaled by the card's
 *    time scarcity), and is then refined by small pointing shifts.
 * 4. Program: the one with the largest expected score, using a band level fitted to saturated hits.
 * 5. The command is the chosen centre minus the learned pointing offset (Hard-mode cards).
 *
 * Learning from results: unsaturated hits give the quality level (scale); saturated hits show the program
 * multiplier exactly and so bracket the band level; E = quality level / band level is the instrument-fault
 * signal (cleanE); hit/miss patterns reveal the pointing offset. The planner only uses the public catalogue,
 * the public score formula, bulletins and its own results.
 *
 * Port of python-pro/planner.py: same constants, same search, same order of floating-point operations,
 * so both agents pick the same actions. Containers whose iteration order matters are Maps.
 */
import {
  FiberGrid,
  Instrument,
  LunarModel,
  Moon,
  SIDEREAL_DEG_PER_SECOND,
  floordiv,
  localSiderealDeg,
  maxHourAngleDeg,
  normalizedAirmass,
  parseUtc,
  pymod,
  pyround,
  pysum,
  radecToAltaz,
  radians,
  shiftAltaz,
  tangentOffsets,
  upperMedian,
  wrap180,
} from "./skymath";

/** Every tunable constant can be overridden with a PRO_<NAME> environment variable. */
export function env(name: string, fallback: number): number {
  const raw = process.env[`PRO_${name}`];
  if (raw === undefined || raw.trim() === "") return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`PRO_${name} must be a number`);
  return value;
}

// --- search -------------------------------------------------------------------------------------------
const LAMBDA_FRAC = env("LAMBDA_FRAC", 0.6); // price of telescope time, as a share of the recent best gain rate
const LAMBDA_EMA = env("LAMBDA_EMA", 0.03);
const SCARCITY_REF = env("SCARCITY_REF", 0.86); // tuning constant: scarcity at which time is priced fully
const SCARCITY_POWER = env("SCARCITY_POWER", 1.0); // time price x min(1, scarcity / SCARCITY_REF) ** power
const TYPICAL_Q = 0.6;
const N_ANCHORS = env("N_ANCHORS", 12); // targets tried as field centres per decision (full speed)
const N_DENSE = env("N_DENSE", 20); // plus centres in the densest patches of remaining science
const DENSE_BIN_DEG = env("DENSE_BIN_DEG", 2.5);
const DENSE_FIBERS = [5, 6, 9, 10];
const REFINE = env("REFINE", 0.1); // local pointing search step (deg); 0 = off
const REFINE_ROUNDS = env("REFINE_ROUNDS", 4);
const REFINE_FIXED_T = env("REFINE_FIXED_T", 1);
const REFINE_STEPS: [number, number][] = [];
if (REFINE > 0) for (const dn of [-1, 0, 1]) for (const de of [-1, 0, 1]) if (dn || de) REFINE_STEPS.push([dn * REFINE, de * REFINE]);
const POOL = env("POOL", 600); // candidates kept after the cheap proxy ranking
const NEIGHBOUR_RADIUS_DEG = env("NEIGHBOUR_RADIUS_DEG", 2.1);
const EDGE_MARGIN_DEG = env("EDGE_MARGIN_DEG", 0.04); // keep targets this far inside their fibre cell
const DURATIONS = [300, 450, 600, 750, 900, 1200, 1500, 1800, 2400, 3000, 3600];
const LEVEL_DURATIONS = [DURATIONS, [300, 600, 900, 1200, 1800, 2400, 3600], [450, 900, 1800, 3600], [900, 1800]];
const MIN_T = env("MIN_T", 0); // shortest exposure considered (unless the night is ending)
const MIN_VISIBLE_SECONDS = 600;
const ALT_MARGIN_DEG = 0.6; // keep targets this far above the altitude limit
// --- value --------------------------------------------------------------------------------------------
const PLAN_FACTOR_SAFETY = env("PLAN_FACTOR_SAFETY", 0.97); // plan as if the sky were 3% worse than estimated
const REQUIRED_BONUS = env("REQUIRED_BONUS", 80.0); // planning value of rescuing one required target (penalty 50)
const REQ_P_LO = env("REQ_P_LO", 0.95); // P(success) ramps from 0 at this share of the needed reach ...
const REQ_P_HI = env("REQ_P_HI", 1.35); // ... to 1 at this share
const REQUEST_MULT = env("REQUEST_MULT", 3.0);
const REQ_CALIB_POWER = env("REQ_CALIB_POWER", 0.0); // per-target calibration after failed tries (0 = off; did not help)
const FORECAST_DISCOUNT = env("FORECAST_DISCOUNT", 0.2);
const REQ_CALENDAR = env("REQ_CALENDAR", 0); // compare with the best future night (public ephemeris), not an ideal sky
const REQ_TIMING = env("REQ_TIMING", 0.85); // attempt a required target when its sky model is >= this share of its best ...
const REQ_TIMING_NIGHTS = env("REQ_TIMING_NIGHTS", 5); // ... unless fewer nights than this are left for it
const REQ_TIMING_DISCOUNT = env("REQ_TIMING_DISCOUNT", 0.3); // required bonus x this while a better moment will come
const URGENCY = env("URGENCY", 1.5); // gain x (1 + URGENCY / nights left for the target)
const PARTIAL_DISCOUNT = env("PARTIAL_DISCOUNT", 1.0); // <1: discount partial exposures of targets the season plan will complete (off)
const PARTIAL_DONE = env("PARTIAL_DONE", 0.9); // reach below this counts as partial
const PARTIAL_NIGHTS = env("PARTIAL_NIGHTS", 3); // no discount when fewer nights than this are left for the target
const PLAN_UTIL = env("PLAN_UTIL", 0.55); // share of fibre-time that ends up useful (for the season plan)
const PLAN_Q = env("PLAN_Q", 0.7); // typical quality for the season plan
const KAPPA = env("KAPPA", 1.0); // convex shaping of science value (1 = linear)
const REQUIRED_SAFE_FACTOR = 0.62; // a required target counts as safe at this estimated factor
const DONE_FACTOR = 0.95; // other targets are done at this factor
// --- pointing offset (Hard-mode cards: a fixed, unannounced offset; its size is not published) ---
const OFFSET_STEPS = 16; // coarse grid half-width in steps of pitch/12 (about a third of the field);
//                          the grid widens by half whenever the best offset sits on its edge
const OFFSET_MIN_MISSES = 6; // start estimating after this many assigned-but-missed targets
const OFFSET_REFINE_EVERY = 10; // observes between fine searches
const OFFSET_FINE_EVIDENCE = 150; // observes used by the fine search
const OFFSET_MARGIN = 8; // adopt an offset only if it explains this many more outcomes
// --- learning -----------------------------------------------------------------------------------------
const SKY_MEMORY_HOURS = 2.0; // quality samples older than this are stale
const BAND_OPT = env("BAND_OPT", 1.0); // declare programs as if the band were this much better (higher programs pay more)
const BAND_HALF_LIFE = env("BAND_HALF_LIFE", 0.0); // hours; recent saturated hits count more when fitting the band (0 = equal)
const BAND_MEMORY_HOURS = 2.0; // saturated hits used to fit the band level
const BAND_FALLBACK = env("BAND_FALLBACK", 0); // with few recent saturated hits, fit the last 8 within BAND_FALLBACK_HOURS
const BAND_FALLBACK_HOURS = env("BAND_FALLBACK_HOURS", 12.0);
const BAND_CONT = env("BAND_CONT", 0); // tie-break the band fit towards the previous fitted level
const CLOSED_KINDS = new Set(["rain", "storm"]);
const SKY_WEATHER_KINDS = new Set(["rain", "storm", "overcast", "haze", "cold_snap"]);
const WEATHER_GATE = env("WEATHER_GATE", 0); // no fault evidence from hours with announced all-sky weather
const NEUTRAL_KINDS = new Set(["earthquake"]); // announced; per the guide it lowers instrument efficiency, not the sky
const BLOCKING_KINDS = new Set(["terrain_obstruction", "rocket_launch"]);
const DIRECTION_AZ: Record<string, number> = { N: 0.0, NE: 45.0, E: 90.0, SE: 135.0, S: 180.0, SW: 225.0, W: 270.0, NW: 315.0 };

const azDistance = (a: number, b: number): number => Math.abs(wrap180(a - b));

/** append to a bounded queue (Python deque(maxlen=...)) */
function pushBounded<T>(queue: T[], item: T, maxlen: number): void {
  queue.push(item);
  if (queue.length > maxlen) queue.splice(0, queue.length - maxlen);
}

function bisectLeft(values: number[], x: number): number {
  let lo = 0;
  let hi = values.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((values[mid] as number) < x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function bisectRight(values: number[], x: number): number {
  let lo = 0;
  let hi = values.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (x < (values[mid] as number)) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

/** Descending order of (value, index) pairs, as Python sorts tuples with reverse=True. */
const byValueThenIndexDesc = (a: [number, number], b: [number, number]): number => b[0] - a[0] || b[1] - a[1];

export type Program = "DARK" | "BRIGHT" | "BACKUP";
export type Pair = [string, string]; // (event kind, direction)
type Night = [number, number]; // [start, end] in epoch seconds
type BandObs = [number, number, string, boolean, boolean]; // hours, model, program, matched, direction-clean
type OffsetRow = [number, number, number, boolean]; // target alt, az, fibre, hit
type Prediction = {
  model: number;
  band_model: number;
  alt: number;
  az: number;
  pred: number;
  clean: boolean;
  dir_clean: boolean;
  fiber?: number;
};
type Base = [number, number, number, number, number]; // alt, az, lunar, seconds still up, multiplier
type Info = [number, number, number, number, number, number]; // alt, az, model now, model in 30 min, up, multiplier
type Found = [number, number, Map<number, number>, number]; // net, T, pick (fibre -> target), total
type Best = [number, number, number, number, Map<number, number>, number]; // net, alt, az, T, pick, total

export interface ObserveAction {
  action: "observe";
  pointing: { alt_deg: number; az_deg: number };
  assignments: Record<string, string>;
  duration_seconds: number;
  program: Program;
  reason?: string;
}

type Payload = any; // protocol JSON (participant guide), read defensively

export class Planner {
  readonly lat: number;
  readonly lon: number;
  readonly minAlt: number;
  readonly nights: Night[];
  readonly surveyEnd: number;
  readonly slotSeconds: number;
  readonly grid: FiberGrid;
  readonly minExposure: number;
  readonly maxExposure: number;
  readonly f0t0: number;
  readonly q0: number;
  readonly airmassExponent: number;
  readonly bands: Record<string, number>;
  readonly multipliers: Record<Program, number>;
  readonly mismatch: number;
  readonly lunarModel: LunarModel;

  readonly ids: string[];
  readonly indexOf = new Map<string, number>();
  readonly ra: number[];
  readonly dec: number[];
  readonly flux: number[];
  readonly weight: number[];
  readonly required: boolean[];
  private readonly sinDec: number[];
  private readonly cosDec: number[];
  private readonly sinLat: number;
  private readonly cosLat: number;
  readonly hmax: number[];
  factor: number[]; // best estimated exposure factor so far
  cur: number[]; // best realised score / weight so far
  rateEma = 0.0; // recent best gain rate (the time price follows it)
  eHours: [number, number, number[]][] = []; // (hour, night, [E samples]): quality level / band level
  private eRatios: [number, number][] = []; // (hours, quality ratio) of hits outside announced directional events (maxlen 400)
  private misses: number[]; // assigned but not hit (e.g. too close to a fibre edge)
  private vcache: number[] | null = null; // value(i) per target; null = rebuild all
  private vdirty = new Set<number>();
  private attempts: number[]; // required hits that still ended below factor 0.5
  private active: number[];
  private readonly altA: number[];
  private readonly altB: number[];
  private readonly altC: number[];
  private readonly sinAltLimit: number;
  private cells = new Map<number, [number, number][]>();
  private cellRas = new Map<number, number[]>();
  private nightBest = new Map<number, number[]>();
  firstNight: number[] = [];
  lastNight: number[] = [];
  private readonly idealModel: number[];
  readonly scarcity: number;
  readonly lambdaFrac: number;

  scale = 1.0; // learned sky quality relative to the clear-sky model
  private priorScale = 1.0; // long-run median, used when recent samples are missing
  private samples: [number, number][] = []; // (hours, ratio) of recent unsaturated hits (maxlen 24)
  private allRatios: number[] = []; // maxlen 400
  private pendingNight = -1;
  private pendingCmd: [number, number] | null = null;
  badForecast = false; // set by the agent from tonight's forecast
  private reqCalib = new Map<number, number>(); // required target -> achieved / predicted on its last failed try
  offset: [number, number] = [0.0, 0.0]; // learned pointing offset (deg): actual = command + offset
  private offsetEvidence: [[number, number], OffsetRow[]][] = []; // (command, rows) per observe (maxlen 400)
  private offsetScores: number[] | null = null;
  private readonly offsetStep: number;
  private offsetSteps = OFFSET_STEPS;
  private offsetGrid: [number, number][];
  private offsetMisses = 0;
  private offsetUpdates = 0;
  bandLevel: number | null = null;
  private bandObs: BandObs[] = []; // saturated hits (maxlen 300)
  private pending = new Map<string, Prediction>(); // target_id -> prediction for the observe in flight
  private pendingProgram: Program = "BACKUP";
  private pendingDuration = 0;
  private blocked: [number, number][] = []; // (az, alt) where a hit scored zero
  notices: Pair[] = [];
  private terrain = new Set<string>();
  extraAvoid = new Set<string>(); // directions an advisor asked to avoid tonight
  fastLevel = 0;
  private requestBonus = new Map<number, number>();
  private requestThreshold = new Map<number, number>();
  // season plan: targets worth completing (value density w*flux above a cut that fills the capacity)
  private readonly densityOrder: number[];
  private planned: boolean[];
  private planNight = -1;
  private nightIndex = -1;
  private dbgNone: number | null = null;

  constructor(init: Payload, private readonly log: (text: string) => void = () => {}) {
    const site = init.site;
    this.lat = Number(site.latitude_deg);
    this.lon = Number(site.longitude_deg);
    this.minAlt = Number(site.minimum_altitude_deg);
    this.nights = (init.survey.nights as Payload[]).map((n) => [parseUtc(n.observing_start_utc), parseUtc(n.observing_end_utc)]);
    this.surveyEnd = parseUtc(init.survey.end_utc);
    this.slotSeconds = Math.trunc(Number(init.survey.slot_seconds));
    const instrument = init.instrument as Instrument;
    this.grid = new FiberGrid(instrument);
    this.minExposure = Math.trunc(Number(instrument.exposure.min_duration_seconds));
    this.maxExposure = Math.trunc(Number(instrument.exposure.max_duration_seconds));
    const score = init.scoring;
    this.f0t0 = Number(score.flux_zero_point) * Number(score.exposure_zero_point_seconds);
    this.q0 = Number(score.q0);
    this.airmassExponent = Number(score.airmass_exponent);
    this.bands = score.program.bands;
    this.multipliers = score.program.multipliers;
    this.mismatch = Number(score.program.mismatch_multiplier);
    this.lunarModel = score.lunar_model;

    const columns: string[] = init.targets.columns;
    const col = (name: string) => columns.indexOf(name);
    const rows: Payload[][] = init.targets.rows;
    const n = rows.length;
    this.ids = rows.map((row) => row[col("target_id")]);
    this.ids.forEach((id, i) => this.indexOf.set(id, i));
    this.ra = rows.map((row) => Number(row[col("ra_deg")]));
    this.dec = rows.map((row) => Number(row[col("dec_deg")]));
    this.flux = rows.map((row) => Number(row[col("feature_flux")]));
    this.weight = rows.map((row) => Number(row[col("science_weight")]));
    this.required = rows.map((row) => Boolean(row[col("required")]));
    this.sinDec = this.dec.map((d) => Math.sin(radians(d)));
    this.cosDec = this.dec.map((d) => Math.cos(radians(d)));
    this.sinLat = Math.sin(radians(this.lat));
    this.cosLat = Math.cos(radians(this.lat));
    this.hmax = this.dec.map((d) => maxHourAngleDeg(d, this.lat, this.minAlt + ALT_MARGIN_DEG));
    this.factor = new Array(n).fill(0.0);
    this.cur = new Array(n).fill(0.0);
    this.misses = new Array(n).fill(0);
    this.attempts = new Array(n).fill(0);
    this.active = [];
    for (let i = 0; i < n; i++) if ((this.hmax[i] as number) > 0.0) this.active.push(i);
    this.altA = this.sinDec.map((s) => this.sinLat * s);
    this.altB = this.cosDec.map((c, i) => this.cosLat * c * Math.cos(radians(this.ra[i] as number)));
    this.altC = this.cosDec.map((c, i) => this.cosLat * c * Math.sin(radians(this.ra[i] as number)));
    this.sinAltLimit = Math.sin(radians(this.minAlt + ALT_MARGIN_DEG));
    this.buildIndex();
    this.buildWindows();
    // best sky model a target can ever get: at transit, Moon down (used to time faint required targets)
    this.idealModel = this.dec.map(
      (d) => 1.0 / (this.q0 * normalizedAirmass(Math.max(1.0, 90.0 - Math.abs(d - this.lat))) ** this.airmassExponent),
    );
    this.buildRequiredCalendar();
    // Time scarcity: fibre-seconds the catalogue needs (typical sky) / night seconds on offer. A time-rich
    // season should price telescope time lower than a tight one.
    const nightSeconds = pysum(this.nights.map(([start, end]) => end - start));
    const need = pysum(this.flux.map((f) => Math.min(this.maxExposure, this.f0t0 / (Math.max(f, 1e-3) * TYPICAL_Q)))) / this.grid.n;
    this.scarcity = need / Math.max(1.0, nightSeconds);
    this.lambdaFrac = LAMBDA_FRAC * Math.min(1.0, Math.max(0.2, this.scarcity / SCARCITY_REF) ** SCARCITY_POWER);
    log(`planner: scarcity ${this.scarcity.toFixed(2)}, time price fraction ${this.lambdaFrac.toFixed(2)}`);

    this.offsetStep = this.grid.pitch / 12.0;
    this.offsetGrid = this.makeOffsetGrid();
    this.densityOrder = [...Array(n).keys()].sort((a, b) => {
      const va = -(this.weight[a] as number) * (this.flux[a] as number);
      const vb = -(this.weight[b] as number) * (this.flux[b] as number);
      return va - vb || a - b; // Python's sort is stable
    });
    this.planned = new Array(n).fill(false);
  }

  // --- precomputation --------------------------------------------------------------------------

  private buildIndex(): void {
    this.cells = new Map();
    for (const i of this.active) {
      const key = Math.floor(this.dec[i] as number);
      let band = this.cells.get(key);
      if (band === undefined) this.cells.set(key, (band = []));
      band.push([this.ra[i] as number, i]);
    }
    for (const [key, band] of this.cells) {
      band.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      this.cellRas.set(
        key,
        band.map(([ra]) => ra),
      );
    }
  }

  /** Per night, the best public sky model (airmass + Moon, no weather) a required target can get.
   *  Uses only geometry and the lunar ephemeris: when the target is highest during that night's window. */
  private buildRequiredCalendar(): void {
    this.nightBest = new Map();
    if (!REQ_CALENDAR) return;
    const lsts = this.nights.map(([start, end]) => [start, localSiderealDeg(start, this.lon), end - start] as const);
    const moons = new Map<string, Moon>();
    for (let i = 0; i < this.ids.length; i++) {
      if (!this.required[i] || (this.hmax[i] as number) <= 0.0) continue;
      const row: number[] = [];
      lsts.forEach(([start, l0, span], k) => {
        // seconds after night start when hour angle is closest to 0
        const ha0 = wrap180(l0 - (this.ra[i] as number));
        const t = Math.min(Math.max(-ha0 / SIDEREAL_DEG_PER_SECOND, 0.0), span);
        const ha = wrap180(ha0 + t * SIDEREAL_DEG_PER_SECOND);
        if (Math.abs(ha) > (this.hmax[i] as number)) {
          row.push(0.0);
          return;
        }
        const half = Math.trunc(floordiv(t, 1800));
        const key = `${k},${half}`;
        let moon = moons.get(key);
        if (moon === undefined) {
          const moment = start + (half + 0.5) * 1800;
          moon = new Moon(moment, localSiderealDeg(moment, this.lon), this.lat, this.lunarModel);
          moons.set(key, moon);
        }
        const [alt] = radecToAltaz(this.ra[i] as number, this.dec[i] as number, l0 + t * SIDEREAL_DEG_PER_SECOND, this.lat);
        row.push(
          moon.lunarFactor(this.ra[i] as number, this.dec[i] as number) /
            (this.q0 * normalizedAirmass(Math.max(alt, 1.0)) ** this.airmassExponent),
        );
      });
      this.nightBest.set(i, row);
    }
  }

  bestFutureModel(i: number, nightIndex: number): number {
    const row = this.nightBest.get(i);
    if (!row || row.length === 0 || nightIndex + 1 >= row.length) return 0.0;
    return Math.max(...row.slice(nightIndex + 1));
  }

  neighbours(ra: number, dec: number, radius: number): number[] {
    const found: number[] = [];
    const cosDec = Math.max(0.05, Math.cos(radians(Math.min(89.0, Math.abs(dec) + radius))));
    const width = radius / cosDec;
    for (let key = Math.floor(dec - radius); key <= Math.floor(dec + radius); key++) {
      const band = this.cells.get(key);
      if (!band || band.length === 0) continue;
      const ras = this.cellRas.get(key) as number[];
      let spans: [number, number][] = [[ra - width, ra + width]];
      const first = spans[0] as [number, number];
      if (first[0] < 0) spans = [[0.0, first[1]], [first[0] + 360.0, 360.0]];
      else if (first[1] >= 360) spans = [[first[0], 360.0], [0.0, first[1] - 360.0]];
      for (const [low, high] of spans) {
        const stop = bisectRight(ras, high);
        for (let k = bisectLeft(ras, low); k < stop; k++) found.push((band[k] as [number, number])[1]);
      }
    }
    return found;
  }

  /** First and last night on which each target has at least 20 minutes above the limit. */
  private buildWindows(): void {
    const need = 20 * 60 * SIDEREAL_DEG_PER_SECOND;
    const spans = this.nights.map(([start, end]) => [localSiderealDeg(start, this.lon), (end - start) * SIDEREAL_DEG_PER_SECOND] as const);
    this.firstNight = new Array(this.ra.length).fill(this.nights.length);
    this.lastNight = new Array(this.ra.length).fill(-1);
    for (const i of this.active) {
      const h = this.hmax[i] as number;
      spans.forEach(([l0, span], k) => {
        let overlap: number;
        if (h >= 180.0) overlap = span;
        else {
          const a = pymod((this.ra[i] as number) - h - l0, 360.0);
          overlap = Math.max(0.0, Math.min(span, a + 2 * h) - a) + Math.max(0.0, Math.min(span, a - 360.0 + 2 * h));
        }
        if (overlap >= need) {
          if ((this.firstNight[i] as number) > k) this.firstNight[i] = k;
          this.lastNight[i] = k;
        }
      });
    }
  }

  // --- messages and results --------------------------------------------------------------------

  onMessages(messages: Payload[], latestBulletin: Payload): void {
    for (const message of messages) {
      const kind = message?.record_type;
      if (kind === "bulletin" && message.initial) {
        for (const notice of message.notices ?? []) {
          if (notice?.event_kind === "terrain_obstruction") this.terrain.add(notice.direction ?? "");
        }
      } else if (kind === "state_resync") {
        this.resync(message);
      }
    }
    const notices: Pair[] = [];
    const seen = new Set<string>();
    for (const n of latestBulletin?.notices ?? []) {
      if (n?.event_kind === "terrain_obstruction") continue;
      const pair: Pair = [n?.event_kind ?? "", n?.direction ?? ""];
      const key = pair.join("\u0000");
      if (!seen.has(key)) {
        seen.add(key);
        notices.push(pair);
      }
    }
    this.notices = notices;
  }

  /** Turn the current all-or-nothing request rewards into per-target planning values. */
  onRequests(requests: Payload[]): void {
    const old = this.requestBonus;
    this.requestBonus = new Map();
    this.requestThreshold = new Map();
    for (const request of requests) {
      // A request that already met its minimum still appears until its deadline
      // with remaining_count 0; its reward is settled, so it adds no value.
      const remaining = Math.trunc(Number(request.remaining_count ?? request.minimum_completed));
      if (remaining <= 0) continue;
      const completed = new Set<string>(request.completed_target_ids ?? []);
      const unit = (REQUEST_MULT * Number(request.completion_reward)) / remaining;
      const threshold = Number(request.completion_factor_threshold);
      for (const targetId of request.target_ids) {
        if (completed.has(targetId) || !this.indexOf.has(targetId)) continue;
        const i = this.indexOf.get(targetId) as number;
        // Overlapping requests: the marginal rewards add up; the combined gain is
        // only collectible at the highest threshold of the contributing requests.
        this.requestBonus.set(i, (this.requestBonus.get(i) ?? 0.0) + unit);
        this.requestThreshold.set(i, Math.max(this.requestThreshold.get(i) ?? 0.0, threshold));
        if ((this.hmax[i] as number) > 0.0 && !this.active.includes(i)) this.active.push(i);
      }
    }
    let changed = old.size !== this.requestBonus.size;
    if (!changed) for (const [k, v] of old) if (this.requestBonus.get(k) !== v) changed = true;
    if (changed) {
      for (const k of old.keys()) this.vdirty.add(k);
      for (const k of this.requestBonus.keys()) this.vdirty.add(k);
    }
  }

  /** Part of the recent data was lost: restart the factor estimates from the engine's best scores. */
  private resync(message: Payload): void {
    const best = new Map<string, number>();
    for (const row of message.best_scores ?? []) best.set(row.target_id, Number(row.best_score));
    const top = Math.max(...Object.values(this.multipliers));
    this.ids.forEach((targetId, i) => {
      const score = best.get(targetId) ?? 0.0;
      this.factor[i] = score > 0 ? Math.min(1.0, score / ((this.weight[i] as number) * top)) : 0.0;
      this.cur[i] = score / (this.weight[i] as number);
    });
    this.active = [];
    for (let i = 0; i < this.ids.length; i++) if ((this.hmax[i] as number) > 0.0) this.active.push(i);
    this.vcache = null;
    this.pending = new Map();
    this.log(`state_resync: ${best.size} targets keep a score; plan rebuilt`);
  }

  siteClosed(): boolean {
    return this.notices.some(([kind, direction]) => CLOSED_KINDS.has(kind) && direction === "ALL");
  }

  allSkyWeather(): boolean {
    return this.notices.some(([kind, direction]) => direction === "ALL" && SKY_WEATHER_KINDS.has(kind));
  }

  allSkyNotice(): boolean {
    return this.notices.some(([kind, direction]) => direction === "ALL" && !NEUTRAL_KINDS.has(kind));
  }

  /** Update factor estimates and the sky-quality estimate from the previous observe. */
  onResult(result: Payload, now: number, hours: number): void {
    void now;
    if (!result || result.action !== "observe" || this.pending.size === 0) {
      this.pending = new Map();
      return;
    }
    const hits = new Map<string, number>();
    for (const hit of result.hits ?? []) hits.set(hit.target_id, Number(hit.score));
    const anyPositive = [...hits.values()].some((score) => score > 0);
    const declared = this.multipliers[this.pendingProgram];
    for (const [targetId, prediction] of this.pending) {
      const i = this.indexOf.get(targetId) as number;
      this.vdirty.add(i);
      const score = hits.get(targetId);
      if (score === undefined) {
        this.misses[i] = (this.misses[i] as number) + 1; // a miss: the target did not land on its fibre glass
        continue;
      }
      if (score <= 0.0) {
        if (anyPositive) this.blocked.push([prediction.az, prediction.alt]);
        continue;
      }
      // score = weight * factor * multiplier; the multiplier is `declared` if the program matched.
      // A saturated hit (factor = 1) shows the multiplier exactly, so it tells whether the sky's
      // program band matched the declared program. Instrument efficiency does not enter the band.
      const w = this.weight[i] as number;
      const multiplierSeen = score / w;
      this.cur[i] = Math.max(this.cur[i] as number, multiplierSeen);
      if (Math.abs(multiplierSeen - declared) < 2e-4) {
        pushBounded(this.bandObs, [hours, prediction.model, this.pendingProgram, true, prediction.dir_clean], 300);
      } else if (Math.abs(multiplierSeen - this.mismatch) < 2e-4) {
        pushBounded(this.bandObs, [hours, prediction.model, this.pendingProgram, false, prediction.dir_clean], 300);
      }
      const factorIfMatch = score / (w * declared);
      const factorIfMiss = score / (w * this.mismatch);
      const ratioMatch = (factorIfMatch * this.f0t0) / ((this.flux[i] as number) * this.pendingDuration * prediction.model);
      const matched = this.band(ratioMatch * prediction.band_model) === this.pendingProgram;
      const factor = matched ? factorIfMatch : factorIfMiss;
      this.factor[i] = Math.max(this.factor[i] as number, Math.min(1.0, factor));
      if (this.required[i] && (this.factor[i] as number) < 0.5) {
        const pred = prediction.pred ?? 0.0;
        if (pred > 0 && factor < 0.97) this.reqCalib.set(i, Math.min(1.0, Math.max(0.3, factor / pred)) ** REQ_CALIB_POWER);
        this.attempts[i] = (this.attempts[i] as number) + 1; // not enough yet: lower its priority a little for next time
      }
      if (factor < 0.97) {
        const ratio = (factor * this.f0t0) / ((this.flux[i] as number) * this.pendingDuration * prediction.model);
        pushBounded(this.samples, [hours, ratio], 24);
        pushBounded(this.allRatios, ratio, 400);
        if (prediction.dir_clean) pushBounded(this.eRatios, [hours, ratio], 400);
      }
    }
    this.offsetEvidenceFrom(hits);
    this.pending = new Map();
    this.updateScale(hours);
  }

  // --- pointing offset (Hard-mode cards) ----------------------------------------------------------------

  /** Hard-mode cards add a hidden fixed offset to every pointing (participant guide: actual = command +
   *  (d_alt, d_az)). Each assigned target's hit or miss is evidence; keep a score for each candidate offset
   *  on a grid and adopt the best one once it clearly explains the misses better than no offset. */
  private offsetEvidenceFrom(hits: Map<string, number>): void {
    if (this.pending.size === 0 || this.pendingCmd === null) return;
    const rows: OffsetRow[] = [];
    for (const [targetId, p] of this.pending) if (p.fiber !== undefined) rows.push([p.alt, p.az, p.fiber, hits.has(targetId)]);
    if (rows.length === 0) return;
    const missed = rows.filter((row) => !row[3]).length;
    this.offsetMisses += missed;
    pushBounded(this.offsetEvidence, [this.pendingCmd, rows], 400);
    if (this.offsetMisses < OFFSET_MIN_MISSES) return;
    if (this.offsetScores === null) this.rescoreOffsets(this.offsetEvidence);
    else this.scoreOffsets(this.pendingCmd, rows);
    this.offsetUpdates += 1;
    if (this.offsetUpdates % OFFSET_REFINE_EVERY === 1 || OFFSET_REFINE_EVERY <= 1) this.refineOffset();
  }

  private consistent(cmd: [number, number], rows: OffsetRow[], dAlt: number, dAz: number): number {
    const cAlt = cmd[0] + dAlt;
    const cAz = pymod(cmd[1] + dAz, 360.0);
    let ok = 0;
    for (const [tAlt, tAz, fiber, hit] of rows) {
      const offsets = tangentOffsets(tAlt, tAz, cAlt, cAz);
      const fib = offsets !== null ? this.grid.classify(offsets[0], offsets[1])[0] : null;
      if ((fib === fiber) === hit) ok += 1;
    }
    return ok;
  }

  private makeOffsetGrid(): [number, number][] {
    const n = this.offsetSteps;
    const step = this.offsetStep;
    const grid: [number, number][] = [];
    for (let i = -n; i <= n; i++) for (let j = -n; j <= n; j++) grid.push([step * i, step * j]);
    return grid;
  }

  private rescoreOffsets(evidence: [[number, number], OffsetRow[]][]): void {
    this.offsetScores = new Array(this.offsetGrid.length).fill(0);
    for (const [cmd, past] of evidence) this.scoreOffsets(cmd, past);
  }

  private scoreOffsets(cmd: [number, number], rows: OffsetRow[]): void {
    const scores = this.offsetScores as number[];
    this.offsetGrid.forEach(([dAlt, dAz], k) => {
      scores[k] = (scores[k] as number) + this.consistent(cmd, rows, dAlt, dAz);
    });
  }

  private bestOffsetIndex(): number {
    const scores = this.offsetScores as number[];
    let k = 0;
    for (let m = 1; m < scores.length; m++) if ((scores[m] as number) > (scores[k] as number)) k = m;
    return k;
  }

  private refineOffset(): void {
    let k = this.bestOffsetIndex();
    let [baseAlt, baseAz] = this.offsetGrid[k] as [number, number];
    const n = this.offsetSteps;
    const edge = Math.max(Math.abs(baseAlt), Math.abs(baseAz)) >= (n - 0.5) * this.offsetStep;
    if (edge && (n + 1) * this.offsetStep < this.grid.fov / 2.0) {
      // the best candidate sits on the edge of the grid: the offset may be larger, widen the search
      this.offsetSteps = Math.trunc(n * 1.5) + 1;
      this.offsetGrid = this.makeOffsetGrid();
      this.rescoreOffsets(this.offsetEvidence.slice(-100));
      this.log(`pointing offset: search widened to +-${(this.offsetSteps * this.offsetStep).toFixed(2)} deg`);
      k = this.bestOffsetIndex();
      [baseAlt, baseAz] = this.offsetGrid[k] as [number, number];
    }
    const evidence = this.offsetEvidence.slice(-OFFSET_FINE_EVIDENCE);
    let zero = 0;
    for (const [cmd, rows] of evidence) zero += this.consistent(cmd, rows, 0.0, 0.0);
    const scored: [number, number, number][] = [];
    const fine = this.offsetStep / 5.0;
    for (let i = -6; i < 7; i++) {
      for (let j = -6; j < 7; j++) {
        const dAlt = baseAlt + fine * i;
        const dAz = baseAz + fine * j;
        let total = 0;
        for (const [cmd, rows] of evidence) total += this.consistent(cmd, rows, dAlt, dAz);
        scored.push([total, dAlt, dAz]);
      }
    }
    const top = Math.max(...scored.map(([score]) => score));
    if (top - zero < OFFSET_MARGIN) return;
    // several offsets often explain the evidence equally well: take the centre of that set
    const tied = scored.filter(([score]) => score === top).map(([, a, z]) => [a, z] as [number, number]);
    const centre: [number, number] = [
      pyround(pysum(tied.map(([a]) => a)) / tied.length, 3),
      pyround(pysum(tied.map(([, z]) => z)) / tied.length, 3),
    ];
    if (Math.abs(centre[0] - this.offset[0]) + Math.abs(centre[1] - this.offset[1]) < 0.005) return;
    const total = evidence.reduce((sum, [, rows]) => sum + rows.length, 0);
    const sign = (v: number) => (v >= 0 ? "+" : "") + v.toFixed(3);
    this.log(
      `pointing offset: alt ${sign(centre[0])} az ${sign(centre[1])} deg explains ${top}/${total} ` +
        `fibre outcomes (no offset: ${zero}; ${tied.length} equally good grid points)`,
    );
    if (this.offset[0] === 0.0 && this.offset[1] === 0.0) {
      this.misses = new Array(this.ids.length).fill(0); // the misses were the offset, not the targets
      this.vcache = null;
    }
    this.offset = centre;
  }

  /** Sky level for the program band, fitted to recent saturated hits (they show the multiplier exactly).
   *  Falls back to the quality-based estimate; an unreported instrument fault lowers quality but not the band. */
  calibratedBandScale(hours: number): number {
    const guess = this.scale / 0.95;
    let recent = this.bandObs.filter((item) => item[0] >= hours - BAND_MEMORY_HOURS);
    if (recent.length < 4 && BAND_FALLBACK) {
      // few saturated hits lately (poor quality, or an instrument fault): the band does not follow the
      // instrument, so keep fitting the latest saturated hits instead of the quality level
      recent = this.bandObs.filter((item) => item[0] >= hours - BAND_FALLBACK_HOURS).slice(-8);
    }
    if (recent.length < 4) return guess;
    // among equally consistent levels prefer the one closest to the last fitted level (the sky band moves
    // with the weather, not with the instrument), else to the quality-based guess
    const centre = BAND_CONT && this.bandLevel ? this.bandLevel : guess;
    const weights = BAND_HALF_LIFE > 0 ? recent.map(([h]) => 0.5 ** ((hours - h) / BAND_HALF_LIFE)) : recent.map(() => 1.0);
    let bestKey: [number, number] | null = null;
    let bestW = guess;
    for (let step = -25; step < 31; step++) {
      const w = centre * 1.06 ** step;
      const okTerms: number[] = [];
      recent.forEach(([, model, program, matched], k) => {
        if ((this.band(model * w) === program) === matched) okTerms.push(weights[k] as number);
      });
      const key: [number, number] = [pyround(pysum(okTerms), 6), -Math.abs(step)];
      if (bestKey === null || key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) {
        bestKey = key;
        bestW = w;
      }
    }
    this.bandLevel = bestW;
    return bestW;
  }

  /** E = clean-sky quality level / clean-sky band level over the last BAND_MEMORY_HOURS (1 = consistent). */
  cleanE(hours: number): number | null {
    const ratios = this.eRatios.filter(([h]) => h >= hours - BAND_MEMORY_HOURS).map(([, r]) => r);
    const obs = this.bandObs.filter((item) => item[0] >= hours - BAND_MEMORY_HOURS && item[4]);
    if (ratios.length < 4 || obs.length < 4) return null;
    const guess = upperMedian(ratios) / 0.95;
    let bestKey: [number, number] | null = null;
    let bestW = guess;
    for (let step = -20; step < 31; step++) {
      const w = guess * 1.06 ** step;
      let ok = 0;
      for (const [, model, program, matched] of obs) if ((this.band(model * w) === program) === matched) ok += 1;
      const key: [number, number] = [ok, -Math.abs(step)];
      if (bestKey === null || key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) {
        bestKey = key;
        bestW = w;
      }
    }
    return Math.min(1.0, guess / bestW);
  }

  /** Sky quality now = median of recent samples; fall back to the long-run median when stale. */
  updateScale(hours: number): void {
    if (this.allRatios.length >= 8) this.priorScale = upperMedian(this.allRatios);
    const recent = this.samples.filter(([when]) => when >= hours - SKY_MEMORY_HOURS).map(([, ratio]) => ratio);
    this.scale = recent.length >= 4 ? Math.max(0.05, upperMedian(recent)) : this.priorScale;
  }

  band(qBand: number): Program {
    if (qBand >= Number(this.bands.DARK)) return "DARK";
    if (qBand >= Number(this.bands.BRIGHT)) return "BRIGHT";
    return "BACKUP";
  }

  // --- anomaly check ---------------------------------------------------------------------------

  /** After a correct report the instrument is repaired: start the quality estimates afresh. */
  forgetQualityHistory(): void {
    this.eHours = [];
    this.eRatios = [];
    this.samples = [];
    this.allRatios = [];
    this.priorScale = 1.0;
  }

  // --- planning --------------------------------------------------------------------------------

  currentNight(now: number): [number, number, number] | null {
    for (let k = 0; k < this.nights.length; k++) {
      const [start, end] = this.nights[k] as Night;
      if (start <= now && now < end) return [k, start, end];
    }
    return null;
  }

  nextNightStart(now: number): number | null {
    for (const [start] of this.nights) if (start > now) return start;
    return null;
  }

  private directionFactor(alt: number, az: number): number {
    let factor = 1.0;
    for (const direction of this.terrain) {
      if (direction in DIRECTION_AZ && alt < 50.0 && azDistance(az, DIRECTION_AZ[direction] as number) <= 60.0) return 0.0;
    }
    for (const [kind, direction] of this.notices) {
      if (!(direction in DIRECTION_AZ)) continue;
      const near = azDistance(az, DIRECTION_AZ[direction] as number) <= 67.5;
      if (BLOCKING_KINDS.has(kind) && near && alt < 62.0) return 0.0;
      if (near && alt < 75.0) factor = Math.min(factor, 0.35);
    }
    for (const direction of this.extraAvoid) {
      if (direction in DIRECTION_AZ && azDistance(az, DIRECTION_AZ[direction] as number) <= 67.5 && alt < 70.0) factor = Math.min(factor, 0.35);
    }
    for (const [blockedAz, blockedAlt] of this.blocked.slice(-40)) {
      if (azDistance(az, blockedAz) <= 12.0 && alt <= blockedAlt + 3.0) factor = Math.min(factor, 0.2);
    }
    return factor;
  }

  value(i: number): number {
    const f = this.factor[i] as number;
    const w = this.weight[i] as number;
    const damp = 0.6 ** (this.misses[i] as number);
    const request = this.requestBonus.get(i) ?? 0.0;
    if (this.required[i]) {
      if (f >= REQUIRED_SAFE_FACTOR) return (w * Math.max(0.0, 1.0 - f * f) + request) * damp;
      return (w * (1.0 - f * f) + REQUIRED_BONUS * (f < 0.5 ? 1.0 : 0.35) + request) * damp;
    }
    return ((f >= DONE_FACTOR ? 0.0 : w * (1.0 - f * f)) + request) * damp;
  }

  /** Which targets will the season complete? Fill the remaining useful fibre-time with targets in
   *  order of value density (w * flux); the rest are fillers whose partial exposures are worth taking. */
  private seasonPlan(now: number, nightIndex: number): void {
    this.planNight = nightIndex;
    let cap = pysum(this.nights.filter(([, end]) => end > now).map(([start, end]) => Math.max(0.0, end - Math.max(start, now))));
    cap *= this.grid.n * PLAN_UTIL;
    this.planned = new Array(this.ids.length).fill(false);
    let n = 0;
    for (const i of this.densityOrder) {
      if (cap <= 0) break;
      if ((this.hmax[i] as number) <= 0.0 || (this.factor[i] as number) >= DONE_FACTOR) continue;
      cap -= Math.min(this.maxExposure, this.f0t0 / (Math.max(this.flux[i] as number, 1e-3) * PLAN_Q));
      this.planned[i] = true;
      n += 1;
    }
    this.log(`season plan: ${n} targets to complete`);
  }

  /** Return an observe action, or null when nothing useful is up. */
  plan(now: number, nightEnd: number, nightIndex: number, hours: number): ObserveAction | null {
    if (PARTIAL_DISCOUNT < 1.0 && nightIndex !== this.planNight) this.seasonPlan(now, nightIndex);
    this.updateScale(hours);
    this.nightIndex = nightIndex;
    const lst = localSiderealDeg(now, this.lon);
    const horizon = Math.min(nightEnd, this.surveyEnd);
    const secondsLeft = horizon - now;
    if (secondsLeft < this.minExposure) return null;
    const lstLater = lst + 1800.0 * SIDEREAL_DEG_PER_SECOND;
    const moon = new Moon(now + 600, lst, this.lat, this.lunarModel);
    const scale = this.scale * PLAN_FACTOR_SAFETY;
    const bandScale = this.calibratedBandScale(hours) * BAND_OPT;
    const eNow = this.cleanE(hours);
    if (eNow !== null && !(WEATHER_GATE && this.allSkyWeather())) {
      const hour = Math.trunc(hours);
      const last = this.eHours[this.eHours.length - 1];
      if (last === undefined || last[0] !== hour) this.eHours.push([hour, nightIndex, []]);
      (this.eHours[this.eHours.length - 1] as [number, number, number[]])[2].push(eNow);
    }
    const minUp = Math.min(MIN_VISIBLE_SECONDS, secondsLeft);
    const level = this.fastLevel;
    const base = new Map<number, Base | null>();
    const visible = new Set<number>();
    const stillActive: number[] = [];
    const minUpDeg = minUp * SIDEREAL_DEG_PER_SECOND;
    const poolSize = [POOL, Math.floor(POOL / 2), Math.floor(POOL / 4), Math.floor(POOL / 8), Math.floor(POOL / 8)][Math.min(level, 4)] as number;
    const proxy: [number, number][] = [];
    const bins = new Map<string, number>();
    const binKeys = new Map<string, [number, number]>();
    const binBest = new Map<string, [number, number]>();
    const cd = this.cosDec;
    if (this.vcache === null) {
      this.vcache = this.ids.map((_, i) => this.value(i));
    } else {
      for (const i of this.vdirty) this.vcache[i] = this.value(i);
    }
    this.vdirty = new Set();
    const vc = this.vcache;
    // sin(alt) = A + B cos(LST) + C sin(LST): no trigonometry per target
    const pa = this.altA;
    const pb = this.altB;
    const pc = this.altC;
    const c1 = Math.cos(radians(lst));
    const s1 = Math.sin(radians(lst));
    const c2 = Math.cos(radians(lst + minUpDeg));
    const s2 = Math.sin(radians(lst + minUpDeg));
    const sinLim = this.sinAltLimit;
    for (const i of this.active) {
      const a = pa[i] as number;
      const sinAlt = a + (pb[i] as number) * c1 + (pc[i] as number) * s1;
      if (sinAlt < sinLim || a + (pb[i] as number) * c2 + (pc[i] as number) * s2 < sinLim) {
        stillActive.push(i); // not up now (or setting soon): keep it, check its value when it rises
        continue;
      }
      const v = vc[i] as number;
      if (v <= 0.0) continue;
      stillActive.push(i);
      visible.add(i);
      // proxy priority: planning value x a rough airmass factor x urgency (no Moon, no direction)
      const nightsLeft = (this.lastNight[i] as number) - nightIndex + 1;
      proxy.push([v * sinAlt ** 0.6 * (1.0 + URGENCY / (nightsLeft > 1 ? nightsLeft : 1)), i]);
      if (N_DENSE) {
        // plain science still to gain here, binned on the sky at roughly one field size
        const k0 = Math.trunc(floordiv((this.ra[i] as number) * (cd[i] as number), DENSE_BIN_DEG));
        const k1 = Math.trunc(floordiv((this.dec[i] as number) + 90.0, DENSE_BIN_DEG));
        const key = `${k0},${k1}`;
        const denseVal = (this.weight[i] as number) * Math.max(0.0, 1.0 - (this.cur[i] as number) / 1.2) * sinAlt ** 0.6;
        bins.set(key, (bins.get(key) ?? 0.0) + denseVal);
        binKeys.set(key, [k0, k1]);
        if (denseVal > (binBest.get(key) ?? [0.0, -1])[0]) binBest.set(key, [denseVal, i]);
      }
    }
    this.active = stillActive;
    if (visible.size === 0) return null;

    const exact = (i: number): Base | null => {
      let item = base.get(i);
      if (item === undefined) {
        const ra = this.ra[i] as number;
        const dec = this.dec[i] as number;
        const ha = pymod(lst - ra + 180.0, 360.0) - 180.0;
        const [alt, az] = radecToAltaz(ra, dec, lst, this.lat);
        const dirf = this.directionFactor(alt, az);
        if (alt < this.minAlt || dirf <= 0.0) {
          base.set(i, null);
          return null;
        }
        const h = this.hmax[i] as number;
        const up = h < 180.0 ? (h - ha) / SIDEREAL_DEG_PER_SECOND : 1e9;
        const lunar = moon.lunarFactor(ra, dec);
        const nightsLeft = Math.max(1, (this.lastNight[i] as number) - nightIndex + 1);
        const damp = 0.6 ** (this.misses[i] as number) * 0.8 ** (this.attempts[i] as number);
        item = [alt, az, lunar, up, (1.0 + URGENCY / nightsLeft) * damp * dirf];
        base.set(i, item);
      }
      return item;
    };

    let pool = (proxy.length > poolSize ? [...proxy].sort(byValueThenIndexDesc).slice(0, poolSize) : proxy).map(([, i]) => i);
    for (const i of pool) exact(i);
    pool = pool.filter((i) => base.get(i) !== null);
    if (pool.length === 0) return null;
    const info = new Map<number, Info>();

    const full = (i: number): Info => {
      let item = info.get(i);
      if (item === undefined) {
        const [alt, az, lunar, up, mult] = base.get(i) as Base;
        const [alt2] = radecToAltaz(this.ra[i] as number, this.dec[i] as number, lstLater, this.lat);
        const m0 = lunar / (this.q0 * normalizedAirmass(Math.max(alt, 1.0)) ** this.airmassExponent);
        const m1 = lunar / (this.q0 * normalizedAirmass(Math.max(alt2, 1.0)) ** this.airmassExponent);
        item = [alt, az, m0, m1, up, mult];
        info.set(i, item);
      }
      return item;
    };

    const topMult = Math.max(...Object.values(this.multipliers));

    /** Convex value of score/weight v: finishing a target beats half-doing it twice, because only
     *  its best exposure counts (a partial exposure is wasted if the target is redone later). */
    const shaped = (v: number): number => (KAPPA !== 1.0 ? topMult * (v / topMult) ** KAPPA : v);
    const ramp = (raw: number): number => Math.min(1.0, Math.max(0.0, (raw - REQ_P_LO) / (REQ_P_HI - REQ_P_LO)));

    const gain = (i: number, T: number): number => {
      const [, , m0, m1, up, mult] = full(i);
      if (up < T) return 0.0;
      const flux = this.flux[i] as number;
      const model = m0 + (m1 - m0) * Math.min(1.0, T / 3600.0);
      const reach = Math.min(1.0, (flux * T * model * scale) / this.f0t0);
      const m = this.multipliers[this.band(model * bandScale)];
      let g = (this.weight[i] as number) * Math.max(0.0, shaped(reach * m) - shaped(this.cur[i] as number));
      if (reach < PARTIAL_DONE && this.planned[i] && (this.lastNight[i] as number) - nightIndex >= PARTIAL_NIGHTS) {
        g *= PARTIAL_DISCOUNT; // it will be completed later: this partial exposure would be wasted
      }
      if (this.required[i] && (this.factor[i] as number) < 0.5) {
        const raw = ((flux * T * model * this.scale) / this.f0t0 / 0.5) * (this.reqCalib.get(i) ?? 1.0);
        let bonus = REQUIRED_BONUS * ramp(raw);
        const targetBest = REQ_CALENDAR ? this.bestFutureModel(i, nightIndex) : (this.idealModel[i] as number);
        if (REQ_TIMING && model < REQ_TIMING * targetBest && (this.lastNight[i] as number) - nightIndex >= REQ_TIMING_NIGHTS) {
          bonus *= REQ_TIMING_DISCOUNT; // a better moment for this target will come
        }
        if (this.badForecast && (this.lastNight[i] as number) - nightIndex >= REQ_TIMING_NIGHTS) {
          bonus *= FORECAST_DISCOUNT; // tonight is forecast bad over the whole sky
        }
        g += bonus;
      }
      const request = this.requestBonus.get(i);
      if (request !== undefined) {
        const threshold = this.requestThreshold.get(i) ?? 0.5;
        const raw = (flux * T * model * this.scale) / this.f0t0 / Math.max(1e-6, threshold);
        g += request * ramp(raw);
      }
      return g * mult;
    };

    /** gain() with the start-of-exposure sky model only (no second alt/az): for ranking. */
    const quick = (i: number, T0: number): [number, number] => {
      const [alt, , lunar, up, mult] = base.get(i) as Base;
      const T = Math.min(T0, up);
      if (T < this.minExposure) return [0.0, T];
      const flux = this.flux[i] as number;
      const model = lunar / (this.q0 * normalizedAirmass(Math.max(alt, 1.0)) ** this.airmassExponent);
      const reach = Math.min(1.0, (flux * T * model * scale) / this.f0t0);
      let g = (this.weight[i] as number) * Math.max(0.0, shaped(reach * this.multipliers[this.band(model * bandScale)]) - shaped(this.cur[i] as number));
      if (reach < PARTIAL_DONE && this.planned[i] && (this.lastNight[i] as number) - nightIndex >= PARTIAL_NIGHTS) g *= PARTIAL_DISCOUNT;
      if (this.required[i] && (this.factor[i] as number) < 0.5) {
        const raw = (flux * T * model * this.scale) / this.f0t0 / 0.5;
        g += REQUIRED_BONUS * ramp(raw);
      }
      const request = this.requestBonus.get(i);
      if (request !== undefined) {
        const raw = (flux * T * model * this.scale) / this.f0t0 / Math.max(1e-6, this.requestThreshold.get(i) ?? 0.5);
        g += request * ramp(raw);
      }
      return [g * mult, T];
    };

    let durations = (LEVEL_DURATIONS[Math.min(level, 3)] as number[]).filter((T) => T <= secondsLeft && T >= MIN_T);
    if (durations.length === 0) durations = [Math.trunc(secondsLeft)];
    const tLong = durations[durations.length - 1] as number;
    const tMid = durations[Math.floor(durations.length / 2)] as number;
    let lam = this.lambdaFrac * this.rateEma;
    let ranked: [number, number][] = [];
    for (const i of pool) {
      const [g1, T1] = quick(i, tLong);
      const [g2, T2] = quick(i, tMid);
      const bestNet = Math.max(g1 - (lam * T1) / 16.0, g2 - (lam * T2) / 16.0);
      if (bestNet > 0) ranked.push([bestNet, i]);
    }
    if (ranked.length === 0) {
      this.rateEma *= 0.9;
      lam = 0.0;
      ranked = pool.map((i) => [quick(i, tLong)[0], i] as [number, number]).filter((item) => item[0] > 0);
    }
    if (ranked.length === 0) return null;
    ranked.sort(byValueThenIndexDesc);
    const nAnchors = [N_ANCHORS, 3, 1, 1][Math.min(level, 3)] as number;
    const anchors = ranked.slice(0, nAnchors).map(([, i]) => i);
    if (N_DENSE && level <= 1 && bins.size > 0) {
      // also try the densest patches of remaining science: fields with no single outstanding target
      const order = [...bins.entries()].sort((a, b) => {
        if (b[1] !== a[1]) return b[1] - a[1];
        const ka = binKeys.get(a[0]) as [number, number];
        const kb = binKeys.get(b[0]) as [number, number];
        return kb[0] - ka[0] || kb[1] - ka[1];
      });
      for (const [key] of order.slice(0, level === 0 ? N_DENSE : 2)) {
        const j = (binBest.get(key) as [number, number])[1];
        if (!anchors.includes(j) && exact(j) !== null) anchors.push(j);
      }
    }
    const allFibers = [...Array(this.grid.n).keys()];
    const fibers = [allFibers, allFibers, [5, 6, 9, 10], [5]][Math.min(level, 3)] as number[];
    let best: Best | null = null;
    const bestRate = [0.0];

    /** Best [net, T, pick, total] for one pointing, or null. */
    const evaluate = (cAlt: number, cAz: number, near: number[], durs: number[]): Found | null => {
      const cells = new Map<number, number[]>();
      for (const j of near) {
        const b = base.get(j) as Base;
        const offsets = tangentOffsets(b[0], b[1], cAlt, cAz);
        if (offsets === null) continue;
        const [fib, margin] = this.grid.classify(offsets[0], offsets[1]);
        if (fib === null || margin < EDGE_MARGIN_DEG * (0.5 + 1.5 * (this.misses[j] as number))) continue;
        let js = cells.get(fib);
        if (js === undefined) cells.set(fib, (js = []));
        js.push(j);
      }
      if (cells.size === 0) return null;
      let found: Found | null = null;
      for (const T of durs) {
        let total = 0.0;
        const pick = new Map<number, number>();
        for (const [fib, js] of cells) {
          let g = -Infinity;
          let j = -1;
          for (const candidate of js) {
            const value = gain(candidate, T);
            if (value > g || (value === g && candidate > j)) {
              g = value;
              j = candidate;
            }
          }
          if (g > 0) {
            total += g;
            pick.set(fib, j);
          }
        }
        if (pick.size === 0) continue;
        if (total / T > (bestRate[0] as number)) bestRate[0] = total / T;
        const net = total - lam * T;
        if (found === null || net > found[0]) found = [net, T, pick, total];
      }
      return found;
    };

    let bestNear: number[] = [];
    const nValueAnchors = Math.min(anchors.length, nAnchors);
    anchors.forEach((anchor, rank) => {
      const [aAlt, aAz] = base.get(anchor) as Base;
      const near = this.neighbours(this.ra[anchor] as number, this.dec[anchor] as number, NEIGHBOUR_RADIUS_DEG).filter(
        (j) => visible.has(j) && exact(j) !== null,
      );
      // density anchors mark a patch, not a target to centre: a few central placements, then refine
      for (const fiber of rank < nValueAnchors ? fibers : DENSE_FIBERS) {
        const [dNorth, dEast] = this.grid.fiberCenter(fiber);
        let [cAlt, cAz] = shiftAltaz(aAlt, aAz, -dNorth, -dEast);
        if (!(this.minAlt <= cAlt && cAlt <= 89.0)) continue;
        cAlt = pyround(cAlt, 4);
        cAz = pymod(pyround(cAz, 4), 360.0);
        const found = evaluate(cAlt, cAz, near, durations);
        if (found !== null && (best === null || found[0] > best[0])) {
          best = [found[0], cAlt, cAz, found[1], found[2], found[3]];
          bestNear = near;
        }
      }
    });
    if (best !== null && REFINE_STEPS.length > 0 && level === 0) {
      // local search: nudge the winning pointing to catch targets near the cell edges
      for (let round = 0; round < REFINE_ROUNDS; round++) {
        let improved = false;
        const [, bAlt, bAz] = best as Best;
        for (const [dNorth, dEast] of REFINE_STEPS) {
          let [cAlt, cAz] = shiftAltaz(bAlt, bAz, dNorth, dEast);
          if (!(this.minAlt <= cAlt && cAlt <= 89.0)) continue;
          cAlt = pyround(cAlt, 4);
          cAz = pymod(pyround(cAz, 4), 360.0);
          const current = best as Best;
          const found = evaluate(cAlt, cAz, bestNear, REFINE_FIXED_T ? [current[3]] : durations);
          if (found !== null && found[0] > current[0] + 1e-9) {
            best = [found[0], cAlt, cAz, found[1], found[2], found[3]];
            improved = true;
          }
        }
        if (!improved) break;
      }
    }
    const bestRateHere = bestRate[0] as number;
    this.rateEma = this.rateEma > 0 ? (1 - LAMBDA_EMA) * this.rateEma + LAMBDA_EMA * bestRateHere : bestRateHere;
    const chosen = best as Best | null;
    if (chosen === null || chosen[5] <= 0) {
      if (Math.trunc(hours) !== this.dbgNone) {
        this.dbgNone = Math.trunc(hours);
        this.log(
          `plan none: info=${info.size} ranked=${ranked.length} scale=${this.scale.toFixed(3)} lam=${lam.toFixed(4)} ` +
            `best=${chosen === null ? "None" : `(${chosen[0]},)`}`,
        );
      }
      return null;
    }
    const [, cAlt, cAz, T, pick] = chosen;
    // program: maximise expected score over the assigned targets
    const votes: Record<Program, number> = { DARK: 0.0, BRIGHT: 0.0, BACKUP: 0.0 };
    for (const j of pick.values()) {
      const [, , m0, m1] = info.get(j) as Info;
      const model = m0 + (m1 - m0) * Math.min(1.0, T / 3600.0);
      const reach = Math.min(1.0, ((this.flux[j] as number) * T * model * scale) / this.f0t0);
      votes[this.band(model * bandScale)] += (this.weight[j] as number) * reach + (this.required[j] ? 0.05 * REQUIRED_BONUS : 0.0);
    }
    const totalVotes = pysum(Object.values(votes));
    let program: Program = "DARK";
    let programKey = -Infinity;
    for (const p of ["DARK", "BRIGHT", "BACKUP"] as Program[]) {
      const key = votes[p] * this.multipliers[p] + (totalVotes - votes[p]) * this.mismatch;
      // max over (key, name): a tie goes to the larger name, as Python compares the tuples
      if (key > programKey || (key === programKey && p > program)) {
        programKey = key;
        program = p;
      }
    }
    const clean = !this.allSkyNotice();
    this.pending = new Map();
    for (const j of pick.values()) {
      const [alt, az, m0, m1] = info.get(j) as Info;
      const model = m0 + (m1 - m0) * Math.min(1.0, T / 3600.0);
      const dirClean = this.directionFactor(alt, az) >= 1.0;
      this.pending.set(this.ids[j] as string, {
        model,
        band_model: model / 0.95,
        alt,
        az,
        pred: ((this.flux[j] as number) * T * model * this.scale) / this.f0t0,
        clean: clean && dirClean,
        dir_clean: dirClean,
      });
    }
    this.pendingProgram = program;
    this.pendingDuration = T;
    this.pendingNight = nightIndex;
    for (const [fib, j] of pick) (this.pending.get(this.ids[j] as string) as Prediction).fiber = fib;
    // the mount lands at command + offset: command the desired centre minus the learned offset
    const cmdAlt = pyround(Math.min(90.0, Math.max(0.0, cAlt - this.offset[0])), 4);
    let cmdAz = pyround(pymod(cAz - this.offset[1], 360.0), 4);
    if (cmdAz >= 360.0) cmdAz = 0.0;
    this.pendingCmd = [cmdAlt, cmdAz];
    const assignments: Record<string, string> = {};
    for (const [fib, j] of [...pick.entries()].sort((a, b) => a[0] - b[0])) assignments[String(fib)] = this.ids[j] as string;
    return {
      action: "observe",
      pointing: { alt_deg: cmdAlt, az_deg: cmdAz },
      assignments,
      duration_seconds: Math.trunc(T),
      program,
    };
  }
}
