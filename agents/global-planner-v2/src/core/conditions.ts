/** Weather and instrument state, ported from the current baseline without changing thresholds. */
import { format_utc } from "./astro";
import { appendBounded, median, mod, Payload } from "./compat";
export const PROGRAMS = ["DARK", "BRIGHT", "BACKUP"] as const;
export type Program = (typeof PROGRAMS)[number];
export type Notice = [string, string];
export type ExposureItem = [
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  boolean,
  number,
  number,
  number,
  number,
];
export interface Exposure {
  t0: number;
  teff: number;
  program: Program;
  cmd_alt: number;
  cmd_az: number;
  items: ExposureItem[];
  night: number;
  clear_dirs: boolean;
  notices: Notice[];
  predicted: number;
}
export const COMPASS: Record<string, number> = {
  N: 0,
  NE: 45,
  E: 90,
  SE: 135,
  S: 180,
  SW: 225,
  W: 270,
  NW: 315,
};
const ALL_SKY_PRIOR: Record<string, number> = {
  overcast: 0.42,
  haze: 0.58,
  cold_snap: 0.9,
};
const CLOSING_KINDS = new Set(["rain", "storm"]);
export const SECTOR_MODEL: Record<
  string,
  [number, number, number, number, number]
> = {
  terrain_obstruction: [15, 35, 35, 56, 0],
  rocket_launch: [20, 40, 30, 62, 0],
  overcast: [20, 60, 40, 82, 0.4],
  haze: [20, 60, 40, 82, 0.55],
  rain: [20, 60, 40, 82, 0],
  storm: [20, 60, 40, 82, 0],
  cold_snap: [20, 60, 40, 82, 0.9],
};
export function sector_table(lo: number, hi: number): number[] {
  const table: number[] = [];
  for (let d = 0; d <= 180; d++) {
    let total = 0;
    for (let k = 0; k < 8; k++) {
      const half = lo + ((hi - lo) * (k + 0.5)) / 8;
      total += Math.max(0, Math.min(1, (22.5 + half - d) / 45));
    }
    table.push(total / 8);
  }
  return table;
}
const SECTOR_TABLES = Object.fromEntries(
  Object.entries(SECTOR_MODEL).map(([kind, m]) => [
    kind,
    sector_table(m[0], m[1]),
  ]),
);
export function inside_probability(
  kind: string,
  alt: number,
  az: number,
  centre: number,
): number {
  const model = SECTOR_MODEL[kind];
  if (alt >= model[3]) return 0;
  const d = Math.abs(mod(az - centre + 180, 360) - 180),
    p_az = SECTOR_TABLES[kind][Math.trunc(d)];
  if (p_az <= 0) return 0;
  return (
    p_az * (alt <= model[2] ? 1 : (model[3] - alt) / (model[3] - model[2]))
  );
}
const equalSets = <T>(a: Set<T>, b: Set<T>): boolean =>
  a.size === b.size && [...a].every((x) => b.has(x));
const noticeKey = (pair: Notice): string => JSON.stringify(pair);
export class Terrain {
  SPAN = 80;
  NEAR = 2;
  REACH = 6;
  blocked: [number, number][] = [];
  pending: [number, number][] = [];
  sure = Array(81).fill(-1);
  top = Array(81).fill(-1);
  floor = Array(81).fill(99);
  box: [number, number, number] | null = null;
  constructor(public centre: number) {}
  relative(az: number): number {
    return mod(az - this.centre + 180, 360) - 180;
  }
  _bin(r: number): number {
    return Math.trunc((r + this.SPAN) / 2 + 0.5);
  }
  add(az: number, alt: number, blocked: boolean, confirmed: boolean): void {
    const r = this.relative(az);
    if (Math.abs(r) > this.SPAN || alt > 66) return;
    if (!blocked) {
      for (
        let b = Math.max(0, this._bin(r - 3));
        b <= Math.min(this.floor.length - 1, this._bin(r + 3));
        b++
      )
        this.floor[b] = Math.min(this.floor[b], alt);
      return;
    }
    if (!confirmed) {
      const partner = [...this.pending, ...this.blocked].find(
        (other) => Math.abs(other[0] - r) <= 10 && other[1] >= alt - 6,
      );
      if (partner === undefined) {
        this.pending.push([r, alt]);
        return;
      }
      const i = this.pending.findIndex(
        (p) => p[0] === partner[0] && p[1] === partner[1],
      );
      if (i >= 0) {
        this.pending.splice(i, 1);
        this._trust(partner[0], partner[1]);
      }
    }
    this._trust(r, alt);
  }
  _trust(r: number, alt: number): void {
    this.blocked.push([r, alt]);
    for (
      let b = Math.max(0, this._bin(r - this.REACH));
      b <= Math.min(this.top.length - 1, this._bin(r + this.REACH));
      b++
    )
      this.top[b] = Math.max(this.top[b], alt);
    for (
      let b = Math.max(0, this._bin(r - this.NEAR));
      b <= Math.min(this.top.length - 1, this._bin(r + this.NEAR));
      b++
    )
      this.sure[b] = Math.max(this.sure[b], alt);
    this.box =
      this.box === null
        ? [r, r, alt]
        : [
            Math.min(this.box[0], r),
            Math.max(this.box[1], r),
            Math.max(this.box[2], alt),
          ];
  }
  blocked_probability(az: number, alt: number): number {
    const r = this.relative(az);
    if (this.box === null)
      return inside_probability("terrain_obstruction", alt, az, this.centre);
    if (Math.abs(r) > this.SPAN) return 0;
    const b = this._bin(r);
    if (alt <= this.sure[b]) return 1;
    if (alt >= this.floor[b]) return 0;
    const top = this.top[b];
    if (top >= 0)
      return alt <= top
        ? 0.85
        : alt < top + 10
          ? 0.5 * (1 - (alt - top) / 10)
          : 0;
    const [lo, hi, top_all] = this.box;
    let p_alt: number;
    if (alt <= top_all) p_alt = 1;
    else if (alt < top_all + 10) p_alt = 0.5 * (1 - (alt - top_all) / 10);
    else return 0;
    if (lo <= r && r <= hi) return 0.6 * p_alt;
    const room = Math.max(1, Math.min(40, 75 - (hi - lo))),
      distance = r < lo ? lo - r : r - hi;
    return distance >= room ? 0 : 0.6 * (1 - distance / room) * p_alt;
  }
}
type Reading = [number, number, number, number];
export class Sky {
  b_dark: number;
  b_bright: number;
  ln_dark: number;
  ln_bright: number;
  mult: Record<Program, number>;
  mis: number;
  mu_max: number;
  allowance: number;
  free_left: number;
  W = 1.1;
  e = 0.95;
  rho = this.W * this.e;
  measured = false;
  m_night = -1;
  m_all = new Set<string>();
  m_quake = false;
  tonight: Reading[] = [];
  prev_tail: [number, number, number, boolean, number, number] | null = null;
  night_levels: number[] = [];
  _night_w: number[] = [];
  bulletins: [number, Notice[]][] = [];
  now_notices: Notice[] = [];
  now_all = new Set<string>();
  terrain: Terrain[] = [];
  dir_now: [string, number][] = [];
  quake_now = false;
  site_closed_now = false;
  quake_last_seen = -1e18;
  quake_since = -1e18;
  closed_suspect = false;
  suspect = false;
  e_ref = 0.95;
  quake_onset = -1e18;
  quake_night0 = -1;
  quake_d0: number | null = null;
  debug = false;
  probe: [Program, number] | null = null;
  ruled_out = new Map<Program, [number, number]>();
  since_dark_probe = 0;
  dark_probe_every = 8;
  weather_settled_at = -1e18;
  low_count = 0;
  low_seconds = 0;
  cooldown_until = -1e18;
  reports_correct = 0;
  reports_false = 0;
  constructor(scoring: Payload) {
    const program = scoring.program;
    this.b_dark = Number(program.bands.DARK);
    this.b_bright = Number(program.bands.BRIGHT);
    this.ln_dark = Math.log(this.b_dark);
    this.ln_bright = Math.log(this.b_bright);
    this.mult = {
      DARK: Number(program.multipliers.DARK),
      BRIGHT: Number(program.multipliers.BRIGHT),
      BACKUP: Number(program.multipliers.BACKUP),
    };
    this.mis = Number(program.mismatch_multiplier ?? 1);
    this.mu_max = Math.max(...Object.values(this.mult));
    this.allowance = Math.trunc(
      scoring.reporting?.false_report_free_allowance ?? 0,
    );
    this.free_left = this.allowance;
  }
  bulletin(record: Payload, ts: number): void {
    const pairs = new Map<string, Notice>();
    for (const notice of record.notices ?? []) {
      const kind = notice.event_kind,
        direction = notice.direction;
      if (kind === "terrain_obstruction") {
        const centre = COMPASS[direction];
        if (
          centre !== undefined &&
          this.terrain.every((t) => t.centre !== centre)
        )
          this.terrain.push(new Terrain(centre));
        continue;
      }
      if (typeof kind === "string" && typeof direction === "string") {
        const pair: Notice = [kind, direction];
        pairs.set(noticeKey(pair), pair);
      }
    }
    const timed = [...pairs.values()];
    appendBounded(this.bulletins, [ts, timed], 96);
    this.now_notices = timed;
    this.now_all = new Set(
      timed
        .filter(([k, d]) => d === "ALL" && Object.hasOwn(ALL_SKY_PRIOR, k))
        .map(([k]) => k),
    );
    this.site_closed_now = timed.some(
      ([k, d]) => d === "ALL" && CLOSING_KINDS.has(k),
    );
    this.quake_now = timed.some(([k]) => k === "earthquake");
    if (this.quake_now) {
      if (ts - this.quake_last_seen > 36 * 3600) this.quake_since = ts;
      this.quake_last_seen = ts;
    }
    this.dir_now = timed
      .filter(
        ([k, d]) => Object.hasOwn(COMPASS, d) && Object.hasOwn(SECTOR_MODEL, k),
      )
      .map(([k, d]) => [k, COMPASS[d]]);
  }
  notices_between(start: number, end: number, slot = 900): Notice[] {
    const merged = new Map<string, Notice>();
    for (const [ts, timed] of this.bulletins)
      if (start - slot < ts && ts < end)
        for (const pair of timed) merged.set(noticeKey(pair), pair);
    return [...merged.values()];
  }
  levels(): [number, number] {
    let rho = this.rho,
      w = this.W;
    if (!equalSets(this.now_all, this.m_all)) {
      for (const k of this.now_all)
        if (!this.m_all.has(k)) {
          rho *= ALL_SKY_PRIOR[k];
          w *= ALL_SKY_PRIOR[k];
        }
      for (const k of this.m_all)
        if (!this.now_all.has(k)) {
          rho /= ALL_SKY_PRIOR[k];
          w /= ALL_SKY_PRIOR[k];
        }
      rho = Math.min(rho, 1.5);
      w = Math.min(w, 1.6);
    }
    return [rho, w];
  }
  get directional(): boolean {
    return this.terrain.length > 0 || this.dir_now.length > 0;
  }
  dir_factor(alt: number, az: number): number {
    let factor = 1;
    if (this.terrain.length && alt < 66)
      for (const terrain of this.terrain) {
        const p = terrain.blocked_probability(az, alt);
        if (p >= 1) return 0;
        if (p > 0) factor *= 1 - p;
      }
    for (const [kind, centre] of this.dir_now) {
      const p = inside_probability(kind, alt, az, centre);
      if (p > 0) factor *= 1 - p * (1 - SECTOR_MODEL[kind][4]);
    }
    return factor;
  }
  band_probabilities(level: number): [number, number] {
    if (level <= 0) return [0, 0];
    const x = Math.log(level),
      za = (x - this.ln_dark) / 0.07,
      zb = (x - this.ln_bright) / 0.07;
    return [
      za > -30 ? 1 / (1 + Math.exp(-za)) : 0,
      zb > -30 ? 1 / (1 + Math.exp(-zb)) : 0,
    ];
  }
  expected_multiplier(level: number, _t: number): number {
    const [pd, pb] = this.band_probabilities(level);
    let best = this.mis;
    for (const [name, p] of [
      ["DARK", pd],
      ["BRIGHT", pb - pd],
      ["BACKUP", 1 - pb],
    ] as [Program, number][]) {
      const value = this.mis + (this.mult[name] - this.mis) * p;
      if (value > best) best = value;
    }
    return best;
  }
  choose_program(pairs: [number, number][], t: number): Program {
    if (this.probe !== null && t <= this.probe[1]) {
      const name = this.probe[0];
      this.probe = null;
      this.since_dark_probe = 0;
      return name;
    }
    this.probe = null;
    const totals: Record<Program, number> = { DARK: 0, BRIGHT: 0, BACKUP: 0 },
      levels: number[] = [];
    for (const [weight, level] of pairs) {
      const [pd, pb] = this.band_probabilities(level);
      totals.DARK += weight * pd * (this.mult.DARK - this.mis);
      totals.BRIGHT += weight * (pb - pd) * (this.mult.BRIGHT - this.mis);
      totals.BACKUP += weight * (1 - pb) * (this.mult.BACKUP - this.mis);
      levels.push(level);
    }
    let choice: Program = "DARK";
    for (const name of PROGRAMS)
      if (totals[name] > totals[choice]) choice = name;
    if (choice === "DARK") {
      this.since_dark_probe = 0;
      return choice;
    }
    this.since_dark_probe++;
    if (
      this.since_dark_probe >= this.dark_probe_every &&
      this.night_levels.length >= 2 &&
      levels.length &&
      this.W > 0
    ) {
      const ordered = [...this.night_levels].sort((a, b) => a - b),
        usual = ordered[Math.floor((3 * ordered.length) / 4)];
      if ((usual * median(levels)) / this.W >= this.b_dark * 1.08) {
        this.since_dark_probe = 0;
        return "DARK";
      }
    }
    return choice;
  }
  _band_of(level: number): Program {
    return level >= this.b_dark
      ? "DARK"
      : level >= this.b_bright
        ? "BRIGHT"
        : "BACKUP";
  }
  update(
    rec: Exposure,
    hits: Map<number, number>,
    night: number,
  ): Map<number, [number, number]> {
    const program = rec.program,
      mu = this.mult[program],
      mis = this.mis,
      t0 = rec.t0,
      t1 = t0 + rec.teff;
    const factors = new Map<number, [number, number]>(),
      known_match: number[] = [],
      known_miss: number[] = [],
      unknown: [ExposureItem, number][] = [];
    const ratios: number[] = [],
      bounds: number[] = [],
      zero_items: ExposureItem[] = [],
      live_items: ExposureItem[] = [];
    let n_hit = 0;
    for (const item of rec.items) {
      const j = item[0],
        score = hits.get(j);
      if (score === undefined) continue;
      n_hit++;
      const weight = item[9],
        obs = score / weight,
        tol = 1.2e-6 / weight + 1e-9,
        unit = item[3],
        a = item[4],
        d = item[5];
      if (obs <= tol) {
        zero_items.push(item);
        factors.set(j, [0, 0]);
        continue;
      }
      live_items.push(item);
      if (obs >= mu - tol) {
        known_match.push(a);
        factors.set(j, [1, 1]);
        if (d >= 0.999 && unit > 0) bounds.push(1 / unit);
      } else if (Math.abs(obs - mis) <= tol) {
        known_miss.push(a);
        factors.set(j, [1, 1]);
        if (d >= 0.999 && unit > 0) bounds.push(1 / unit);
      } else if (obs > mis + tol) {
        known_match.push(a);
        const g = obs / mu;
        factors.set(j, [g, g]);
        if (d >= 0.999 && unit > 0) ratios.push(g / unit);
      } else unknown.push([item, obs]);
    }
    if (n_hit === 0) return factors;
    if (zero_items.length === n_hit) {
      const risky = zero_items.filter((item) => item[5] < 0.9).length;
      if (risky * 2 >= n_hit) {
        if (this.terrain.length) {
          let high = zero_items[0];
          for (const item of zero_items) if (item[6] > high[6]) high = item;
          this._mark_horizon(high[6], high[7], true, false);
        }
      } else {
        this.closed_suspect = true;
        this.tonight = [];
        this._closure_explains();
      }
      return factors;
    }
    this.closed_suspect = false;
    if (unknown.length) {
      const evidence = known_match.length > 0 || known_miss.length > 0;
      let verdict: boolean[];
      if (known_match.length && !known_miss.length)
        verdict = Array(unknown.length).fill(true);
      else if (known_miss.length && !known_match.length)
        verdict = Array(unknown.length).fill(false);
      else if (evidence)
        verdict = unknown.map(
          ([item]) =>
            Math.min(
              ...known_match.map((x) => Math.abs(Math.log(item[4] / x))),
            ) <=
            Math.min(...known_miss.map((x) => Math.abs(Math.log(item[4] / x)))),
        );
      else {
        let usable = unknown
          .filter(([item]) => item[3] > 0 && item[5] >= 0.999)
          .map(([item, obs]) => [obs / item[3], item[4]]);
        if (!usable.length)
          usable = unknown
            .filter(([item]) => item[3] > 0)
            .map(([item, obs]) => [obs / item[3], item[4]]);
        let matched = true;
        if (usable.length) {
          const raw = median(usable.map((u) => u[0])),
            a_mid = median(usable.map((u) => u[1])),
            rho1 = raw / mu,
            rho0 = raw / mis;
          const c1 = this._band_of((rho1 / this.e) * a_mid) === program,
            c0 = this._band_of((rho0 / this.e) * a_mid) !== program;
          if (this.suspect) matched = this._band_of(this.W * a_mid) === program;
          else if (c1 !== c0) matched = c1;
          else {
            const ref = Math.max(this.rho, 1e-6);
            matched =
              Math.abs(Math.log(Math.max(rho1, 1e-9) / ref)) <=
              Math.abs(Math.log(Math.max(rho0, 1e-9) / ref));
          }
        }
        verdict = Array(unknown.length).fill(matched);
      }
      for (let i = 0; i < unknown.length; i++) {
        const [item, obs] = unknown[i],
          g = Math.min(1, obs / (verdict[i] ? mu : mis));
        factors.set(item[0], [
          g,
          evidence ? g : Math.min(g, obs / this.mu_max),
        ]);
        if (item[3] > 0 && item[5] >= 0.999) ratios.push(g / item[3]);
      }
    }
    if (this.terrain.length) {
      for (const item of zero_items) this._mark_horizon(item[6], item[7], true);
      for (const item of live_items)
        if (item[6] < 66) this._mark_horizon(item[6], item[7], false);
    }
    const all_now = new Set(
      rec.notices
        .filter(([k, d]) => d === "ALL" && Object.hasOwn(ALL_SKY_PRIOR, k))
        .map(([k]) => k),
    );
    const quake = rec.notices.some(([k]) => k === "earthquake"),
      closing = rec.notices.some(
        ([k, d]) => d === "ALL" && CLOSING_KINDS.has(k),
      );
    if (!equalSets(all_now, this.m_all) || closing)
      this.weather_settled_at = t1 + 2 * 3600;
    let clean = !zero_items.length && ratios.length >= 1,
      partial_closure = false;
    if (ratios.length) {
      let rho_new = median(ratios);
      if (bounds.length) {
        const floor_rho = Math.max(...bounds);
        if (rho_new < floor_rho) {
          if (rho_new < 0.8 * floor_rho) clean = false;
          rho_new = floor_rho;
        }
      }
      const new_night = night !== this.m_night;
      if (this.measured && new_night) this._close_night();
      let reference: Reading | null = null;
      if (!new_night)
        for (const entry of this.tonight)
          if (
            t0 - entry[0] <= 3600 &&
            (reference === null || entry[1] > reference[1])
          )
            reference = entry;
      const tail = this.prev_tail;
      let step = 0.76;
      if (reference !== null && reference[2] > 1.2)
        step = Math.max(0.55, 0.76 - 0.25 * (reference[2] - 1.2));
      if (!this.measured) this.W = rho_new / this.e;
      else if (!new_night && !equalSets(all_now, this.m_all)) {
        this.W = rho_new / this.e;
        this.tonight = [];
      } else if (quake && (!this.m_quake || this.quake_d0 === null)) {
        this.e = Math.max(0.05, Math.min(1, rho_new / this.W));
        this.suspect = false;
        this.quake_onset = t0;
        this.quake_night0 = night;
        this.quake_d0 = Math.max(0, Math.min(0.95, 1 - this.e / 0.95));
        this.tonight = [];
      } else if (quake && night === this.quake_night0 && !this.suspect) {
        this.e = Math.max(0.05, Math.min(1, rho_new / this.W));
        this.quake_d0 = Math.max(0, Math.min(0.95, 1 - this.e / 0.95));
      } else if (this.suspect && rho_new < 0.25 * this.e_ref * this.W) {
        this.closed_suspect = true;
        partial_closure = true;
        this._closure_explains();
      } else if (this.suspect) {
        if (rho_new >= 0.86 * this.e_ref * this.W) {
          this.suspect = false;
          this.e = this.e_ref;
          this.W = rho_new / this.e;
        } else this.e = Math.max(0.05, Math.min(1, rho_new / this.W));
      } else if (new_night) {
        if (
          tail !== null &&
          1 <= night - tail[2] &&
          night - tail[2] <= 3 &&
          !all_now.size &&
          !tail[3] &&
          !(quake && this.quake_since >= tail[5] - 3600) &&
          t0 >= this.weather_settled_at &&
          rho_new < (night === tail[2] + 1 ? 0.6 : 0.5) * tail[0]
        ) {
          this.suspect = true;
          this.e_ref = tail[4];
          this.W = tail[1];
          this.e = Math.max(0.05, Math.min(1, rho_new / tail[1]));
        } else if (quake && this.quake_d0 !== null) {
          const nights_since = Math.max(0, night - this.quake_night0);
          this.e = 0.95 * (1 - this.quake_d0 * Math.exp(-nights_since / 4));
          this.W = rho_new / this.e;
        } else {
          this.e = 0.95;
          this.quake_d0 = null;
          this.W = rho_new / this.e;
        }
      } else if (reference !== null && rho_new < 0.25 * reference[1]) {
        this.closed_suspect = true;
        partial_closure = true;
        this._closure_explains();
      } else if (
        reference !== null &&
        rho_new < step * reference[1] &&
        known_miss.length &&
        !known_match.length &&
        program === "DARK"
      ) {
        this.W = rho_new / this.e;
        this.tonight = [];
      } else if (
        reference !== null &&
        rho_new < step * reference[1] &&
        t0 >= this.weather_settled_at
      ) {
        this.suspect = true;
        this.e_ref = reference[3];
        this.W = reference[2];
        this.e = Math.max(
          0.05,
          Math.min(1, (reference[3] * rho_new) / reference[1]),
        );
      } else this.W = rho_new / this.e;
      if (partial_closure) rho_new = this.rho;
      else if (this.W > 2.2) {
        this.suspect = false;
        this.e = 0.95;
        this.e_ref = 0.95;
        this.W = rho_new / this.e;
      }
      this.rho = rho_new;
      this.measured = true;
      this.m_night = night;
      this.m_all = all_now;
      this.m_quake = quake;
    } else if (bounds.length) {
      const floor_rho = Math.max(...bounds);
      if (floor_rho > this.rho) {
        if (this.suspect) {
          if (floor_rho >= 0.86 * this.e_ref * this.W) {
            this.suspect = false;
            this.e = this.e_ref;
            this.W = floor_rho / this.e;
          } else
            this.e = Math.min(this.e_ref, Math.max(this.e, floor_rho / this.W));
        } else this.W = floor_rho / this.e;
        this.rho = floor_rho;
      }
      if (!this.measured || night !== this.m_night) {
        if (this.measured) this._close_night();
        this.m_night = night;
        this.m_all = all_now;
        this.m_quake = quake;
        this.measured = true;
      }
      if (!zero_items.length && equalSets(all_now, this.m_all) && !this.suspect)
        appendBounded(this.tonight, [t0, floor_rho, this.W, this.e], 6);
    }
    let lower: number | null = null,
      upper: number | null = null;
    if (known_match.length && !known_miss.length) {
      this.ruled_out.clear();
      if (program === "DARK") lower = this.b_dark / Math.min(...known_match);
      else if (program === "BRIGHT") {
        lower = this.b_bright / Math.min(...known_match);
        upper = this.b_dark / Math.max(...known_match);
      } else upper = this.b_bright / Math.max(...known_match);
    } else if (known_miss.length && !known_match.length) {
      const a_ref = median(known_miss);
      this.ruled_out = new Map(
        [...this.ruled_out].filter(([, v]) => v[0] >= t0),
      );
      this.ruled_out.set(program, [t1 + 2.5 * 3600, a_ref]);
      const out = this.ruled_out;
      if (program === "DARK") upper = this.b_dark / a_ref;
      else if (program === "BACKUP") lower = this.b_bright / a_ref;
      if (out.has("BRIGHT")) {
        const a_b = out.get("BRIGHT")![1];
        if (out.has("BACKUP") && out.get("BACKUP")![1] <= a_b * 1.03)
          lower = Math.max(lower || 0, this.b_dark / a_b);
        else if (out.has("DARK") && out.get("DARK")![1] >= a_b * 0.97)
          upper = Math.min(upper || 1e9, this.b_bright / a_b);
        else if (program === "BRIGHT") this.probe = ["DARK", t1 + 2 * 3600];
      }
      if (out.size === 3) this.ruled_out = new Map();
    }
    if (lower !== null && upper !== null && lower >= upper) {
      lower = upper = null;
      this.ruled_out = new Map();
    }
    if (lower !== null && lower > this.W * 1.04) {
      const before = this.e;
      this.W = lower * 1.01;
      this.e = Math.max(0.05, Math.min(1, this.rho / this.W));
      if (quake && this.quake_d0 !== null && night <= this.quake_night0 + 1) {
        const healed = Math.exp(-Math.max(0, night - this.quake_night0) / 4);
        this.quake_d0 = Math.max(
          this.quake_d0,
          Math.min(0.95, (1 - this.e / 0.95) / healed),
        );
      } else if (!this.suspect && this.e < 0.8 * before) {
        this.suspect = true;
        this.e_ref = before;
      }
    }
    if (upper !== null && upper < this.W / 1.04) {
      this.W = upper * 0.99;
      this.e = Math.max(0.05, Math.min(1, this.rho / this.W));
      if (this.suspect && this.e >= 0.86 * this.e_ref) {
        this.suspect = false;
        this.e = this.e_ref;
        this.W = this.rho / this.e;
      }
    }
    if (this.debug)
      process.stderr.write(
        `sky ${format_utc(t0)} T=${rec.teff} ${program} match=${known_match.length} miss=${known_miss.length} unk=${unknown.length} zero=${zero_items.length} nrat=${ratios.length} rho=${ratios.length ? median(ratios) : NaN} W=${this.W} e=${this.e} low=${this.low_count} lower=${lower} upper=${upper}\n`,
      );
    if (ratios.length && clean && !partial_closure) {
      appendBounded(this.tonight, [t0, this.rho, this.W, this.e], 6);
      this._night_w.push(this.W);
      if (
        (rec.clear_dirs ?? true) &&
        t0 >= this.weather_settled_at &&
        this.suspect &&
        this.e < 0.78 * this.e_ref
      ) {
        this.low_count++;
        this.low_seconds += rec.teff;
      }
    }
    if (!this.suspect) {
      this.low_count = 0;
      this.low_seconds = 0;
    }
    return factors;
  }
  _mark_horizon(
    alt: number,
    az: number,
    blocked: boolean,
    confirmed = true,
  ): void {
    if (blocked)
      for (const [kind, centre] of this.dir_now)
        if (
          SECTOR_MODEL[kind][4] === 0 &&
          Math.abs(mod(az - centre + 180, 360) - 180) < 70
        )
          return;
    let nearest = this.terrain[0];
    for (const terrain of this.terrain)
      if (Math.abs(terrain.relative(az)) < Math.abs(nearest.relative(az)))
        nearest = terrain;
    nearest.add(az, alt, blocked, confirmed);
  }
  _closure_explains(): void {
    if (this.suspect && this.low_count <= 2) {
      this.suspect = false;
      this.e = this.e_ref;
      this.low_count = 0;
      this.low_seconds = 0;
    }
  }
  _close_night(): void {
    if (this.tonight.length >= 2) {
      const last = this.tonight.slice(-3);
      this.prev_tail = [
        median(last.map((e) => e[1])),
        median(last.map((e) => e[2])),
        this.m_night,
        this.m_all.size > 0,
        median(last.map((e) => e[3])),
        last[0][0],
      ];
    } else this.prev_tail = null;
    if (this._night_w.length)
      appendBounded(this.night_levels, median(this._night_w), 21);
    this._night_w = [];
    this.tonight = [];
    this.probe = null;
    this.ruled_out = new Map();
    this.low_count = Math.min(this.low_count, 2);
    this.low_seconds = Math.min(this.low_seconds, 2400);
  }
  want_report(t: number, remaining_gain = 0): boolean {
    if (
      t < this.cooldown_until ||
      t - this.quake_onset < 3 * 3600 ||
      t - this.quake_since < 3 * 3600
    )
      return false;
    if (!this.suspect || this.e >= 0.78 * this.e_ref) return false;
    const ratio = this.e / this.e_ref,
      strong = ratio < 0.62;
    let need = 3 + Math.min(4, this.reports_false),
      seconds = 2400;
    if (!strong) {
      need = Math.max(need, 5);
      seconds = 5400;
    } else if (this.quake_now && t - this.quake_since < 60 * 3600) {
      need += 2;
      seconds = 3600;
    } else if (this.free_left >= 2 && this.reports_false === 0) {
      need = 2;
      seconds = 1800;
    }
    if (this.low_count < need || this.low_seconds < seconds) return false;
    if (this.free_left > 0) return true;
    if (!strong || this.low_count < 4 || this.low_seconds < 3600) return false;
    return 0.3 * (100 + (1 - ratio) * remaining_gain) > 0.7 * 150;
  }
  report_result(correct: boolean, t: number): void {
    this.low_count = 0;
    this.low_seconds = 0;
    this.suspect = false;
    this.tonight = [];
    if (correct) {
      this.reports_correct++;
      this.free_left = this.allowance;
      this.e = this.e_ref;
      this.rho = this.W * this.e;
    } else {
      this.reports_false++;
      this.free_left = Math.max(0, this.free_left - 1);
      this.e = this.e_ref;
      this.W = this.rho / this.e;
      this.cooldown_until = t + 8 * 3600;
    }
  }
}
