/** Native Node.js 22 agent for participant-agent-protocol-v4. Uses persistent local workers for the selected models. */
import { once } from "node:events";
import { StringDecoder } from "node:string_decoder";
import { DEG, format_utc, parse_utc } from "./core/astro";
import { asciiJson, mod, Payload } from "./core/compat";
import { Exposure, ExposureItem } from "./core/conditions";
import { OffsetItem } from "./core/pointing";
import { Plan, Planner } from "./core/planner";
import { Knobs, Survey } from "./core/state";
import { BaselineModels, SelectedModelsSession, SELECTED_MODEL_IDS } from "./models";

export const PROTOCOL = "participant-agent-protocol-v4";
export const KNOBS: Knobs = {
  kappa: 0.7,
  price_cap: 0.8,
  rate_hours: 6,
  request_gain: 3,
  request_floor: 2.5,
  urgency_power: 0.5,
  urgency_cap: 3,
  sci_urgency_power: 0.5,
  sci_urgency_cap: 2.5,
  partial_discount: 0.85,
  deep_weight: 0.25,
  dark_probe_every: 8,
  timing_power: 3,
  k_max: 14,
  fine_shifts: 1,
  cpu_share: 0.85,
  wall_share: 0.8,
  calib_shots: 1,
  final_probe: 1,
  log_every: 250,
};
export function cpu_seconds(): number {
  const c = process.cpuUsage();
  return (c.user + c.system) / 1e6;
}
export function monotonic(): number {
  return Number(process.hrtime.bigint()) / 1e9;
}
export function log(...parts: unknown[]): void {
  process.stderr.write(
    parts
      .map((p) => (typeof p === "string" ? p : JSON.stringify(p)))
      .join(" ") + "\n",
  );
}
export function load_knobs(): Knobs {
  const knobs = { ...KNOBS };
  for (const name of Object.keys(knobs)) {
    const raw = process.env["BASE_" + name.toUpperCase()];
    if (raw?.trim()) {
      const value = Number(raw);
      if (!Number.isNaN(value)) knobs[name] = value;
    }
  }
  return knobs;
}
export class Agent {
  knobs: Knobs;
  sv: Survey;
  planner: Planner;
  readonly models: BaselineModels;
  readonly modelSession: SelectedModelsSession;
  /** false: the fault model alone decides reports; true: the rule check must also allow them. */
  readonly faultGuard: boolean;
  modelCompleted: Payload | undefined;
  model_cpu = 0;
  pending: Exposure | null = null;
  night_seen = -1;
  calib_left: number;
  calib_extra = 0;
  final_probe_done = false;
  decisions = 0;
  suffix: number[];
  avg_t = 900;
  k = 8;
  own = 0.02;
  engine = 0.02;
  engine0: [number, number] | null = null;
  entries = 0;
  requests_seen = 0;
  now = 0;
  reply_at: number | null = null;
  t_floor: number;
  misses = 0;
  assigned = 0;
  predicted_gain = 0;
  realised_gain = 0;
  constructor(payload: Payload) {
    this.knobs = load_knobs();
    const started = cpu_seconds();
    this.sv = new Survey(payload, this.knobs);
    this.planner = new Planner(this.sv);
    this.models = new BaselineModels();
    const enabled = [...SELECTED_MODEL_IDS].filter(id => id === "conditions-reliability"
      ? process.env.BASE_CONDITIONS_MODELS !== "off" : process.env.BASE_FAULT_MODEL !== "off");
    if (process.env.BASE_CONDITIONS_MODELS && !["off", "reliability", "conditions-reliability"].includes(process.env.BASE_CONDITIONS_MODELS))
      throw new Error("BASE_CONDITIONS_MODELS must be reliability or off; GRU/MLP are legacy comparison models.");
    if (process.env.BASE_FAULT_GUARD && !["off", "rule"].includes(process.env.BASE_FAULT_GUARD))
      throw new Error("BASE_FAULT_GUARD must be rule or off.");
    this.faultGuard = process.env.BASE_FAULT_GUARD === "rule";
    this.modelSession = new SelectedModelsSession(payload, this.models, enabled);
    const sv = this.sv;
    sv.sky.debug = Boolean(process.env.BASE_DEBUG_SKY);
    sv.sky.dark_probe_every = Math.trunc(this.knobs.dark_probe_every);
    this.calib_left = Math.trunc(this.knobs.calib_shots);
    this.suffix = Array(sv.nights.length + 1).fill(0);
    for (let i = sv.nights.length - 1; i >= 0; i--)
      this.suffix[i] = this.suffix[i + 1] + sv.nights[i][1] - sv.nights[i][0];
    this.t_floor = sv.t_min;
    const cat = sv.cat;
    log(
      `init: ${cat.n} targets, ${cat.required.filter(Boolean).length} required, ${cat.nz} zones, ${cat.nc} season cells, ${sv.nights.length} nights, ${sv.n_fibers} fibres pitch ${sv.pitch.toFixed(4)} deg, price0 ${sv.price.toFixed(5)}/s, cpu ${(cpu_seconds() - started).toFixed(2)}s`,
    );
  }
  _engine_seconds(
    n_observes: number,
    nights_left: number,
    speed: number,
  ): number {
    const sv = this.sv,
      entries = this.entries,
      per_exposure =
        sv.observes >= 10 ? entries / sv.observes : 0.8 * sv.n_fibers;
    const i =
      this.night_seen < 0
        ? sv.nights.length - 1
        : Math.min(this.night_seen, sv.nights.length - 1);
    const done =
      1 -
      (Math.max(0, sv.nights[i][1] - this.now) +
        this.suffix[Math.min(this.night_seen + 1, sv.nights.length)]) /
        sv.total_night_seconds;
    const requests_end =
        done > 0.15
          ? this.requests_seen / done
          : Math.max(this.requests_seen, Math.min(6, sv.nights.length / 14)),
      load_now = entries * (1 + this.requests_seen);
    if (this.engine0 === null && sv.observes >= 40)
      this.engine0 = [this.engine, load_now];
    let slope = 0.25e-6 * speed,
      base = this.engine;
    if (this.engine0 !== null) {
      base = this.engine0[0];
      if (load_now - this.engine0[1] > 30000)
        slope = Math.max(
          0.05e-6 * speed,
          (this.engine - this.engine0[0]) / (load_now - this.engine0[1]),
        );
      base = Math.max(0.002, base - slope * this.engine0[1]);
    }
    const entries_mid = entries + (per_exposure * n_observes) / 2,
      requests_mid = 0.5 * (this.requests_seen + requests_end),
      per_observe = base + slope * entries_mid * (1 + requests_mid);
    return (
      n_observes * per_observe +
      nights_left * 12 * slope * entries_mid * requests_mid
    );
  }
  _pace(payload: Payload, t: number, night: number): void {
    const sv = this.sv,
      clock = payload.wallclock || {},
      cpu_left = Number(
        clock.remaining_real_cpu_seconds ?? clock.remaining_seconds ?? 900,
      ),
      wall_left = clock.wall_remaining_seconds,
      speed = Number(clock.speed_factor || 1);
    this.now = t;
    const remaining =
        Math.max(0, sv.nights[night][1] - t) + this.suffix[night + 1],
      nights_left = sv.nights.length - night,
      n_observes = (remaining / Math.max(this.avg_t, 60)) * 1.05,
      n_other = 2 * nights_left + 5;
    let per_decision =
        (cpu_left * this.knobs.cpu_share) / (n_observes + n_other),
      floor = sv.t_min;
    if (wall_left !== undefined && wall_left !== null) {
      const budget = Number(wall_left) * this.knobs.wall_share,
        minimum = 0.004 * speed,
        spare = budget - this._engine_seconds(n_observes, nights_left, speed),
        per_wall = spare / (n_observes + n_other);
      if (per_wall < per_decision) per_decision = per_wall;
      if (per_wall < minimum) {
        let stretch = 1;
        for (let i = 0; i < 12; i++) {
          stretch *= 1.2;
          const fewer = n_observes / stretch;
          if (
            this._engine_seconds(fewer, nights_left, speed) +
              (fewer + n_other) * minimum <=
            budget
          )
            break;
        }
        floor = Math.trunc(Math.min(sv.t_max, this.avg_t * stretch));
        per_decision = minimum;
      }
      if (Number(wall_left) < 15 || cpu_left < 1.5) {
        per_decision = 0;
        floor = sv.t_max;
      }
    }
    per_decision = Math.max(0, per_decision - this.model_cpu);
    const cheapest = Math.max(0.002, (this.own * 2) / Math.max(this.k, 2));
    if (0 < per_decision && per_decision < cheapest)
      floor = Math.max(
        floor,
        Math.trunc(Math.min(sv.t_max, (this.avg_t * cheapest) / per_decision)),
      );
    const ratio = per_decision / Math.max(this.own, 1e-4);
    this.k = Math.max(
      2,
      Math.min(
        this.knobs.k_max,
        this.k * Math.max(0.6, Math.min(1.4, Math.sqrt(Math.max(ratio, 0)))),
      ),
    );
    this.t_floor = Math.max(sv.t_min, floor);
  }
  _digest(last: Payload): void {
    const rec = this.pending!,
      sv = this.sv,
      cat = sv.cat,
      hits = new Map<number, number>();
    for (const hit of last.hits || []) {
      const j = cat.index.get(hit.target_id);
      if (j !== undefined) hits.set(j, Number(hit.score ?? 0));
    }
    rec.notices = sv.sky.notices_between(rec.t0, rec.t0 + rec.teff, sv.slot);
    const outcomes: OffsetItem[] = [];
    let unexpected = 0;
    for (const item of rec.items) {
      const hit = hits.has(item[0]);
      outcomes.push([item[10], item[11], item[12], item[1], item[2], hit]);
      if (!hit) {
        this.misses++;
        if (item[8]) unexpected++;
      }
    }
    this.assigned += rec.items.length;
    this.entries += hits.size;
    const was_active = sv.offset.active;
    sv.offset.observe(rec.cmd_alt, rec.cmd_az, outcomes, unexpected);
    if (sv.offset.active && (!was_active || unexpected))
      log(
        `pointing: offset estimate alt ${sv.offset.alt.toFixed(4)} az ${sv.offset.az.toFixed(4)} deg (+-${sv.offset.margin_alt.toFixed(4)}/${sv.offset.margin_az.toFixed(4)}), ${unexpected} unexpected misses, mismatch ${sv.offset.best_mismatch}`,
      );
    const factors = sv.sky.update(rec, hits, rec.night),
      rows: [number, number, number, number][] = [];
    for (const [j, score] of hits) {
      const f = factors.get(j);
      if (f !== undefined) rows.push([j, score, f[0], f[1]]);
    }
    const closed =
        hits.size > 0 && [...hits.values()].every((score) => score <= 0),
      index = last.observe_index;
    const gain = sv.apply_hits(
      Number.isInteger(index) ? index : sv.observes,
      rows,
      rec.teff,
      closed,
    );
    if (!closed) {
      this.predicted_gain += rec.predicted;
      this.realised_gain += gain;
    }
    this.modelCompleted = {
      observe_index: Number.isInteger(last.observe_index) ? last.observe_index : sv.observes,
      start_utc: format_utc(rec.t0), end_utc: format_utc(rec.t0 + rec.teff),
      program: rec.program, night: rec.night, pointing: { alt_deg: rec.cmd_alt, az_deg: rec.cmd_az },
      assigned_count: rec.items.length, hits: last.hits || [],
      target_ids: rec.items.map(item => cat.ids[item[0]]), items: rec.items.map(item => item.slice(0, 10)),
      estimated_efficiency: sv.sky.e, suspected_fault: sv.sky.suspect, quake_notice: sv.sky.quake_now,
    };
    this.avg_t += 0.05 * (rec.teff - this.avg_t);
  }
  async decide(payload: Payload): Promise<Payload> {
    const began = this.models.worker.cpuSeconds;
    try { return await this.decideAction(payload); }
    finally {
      const elapsed = this.models.worker.cpuSeconds - began;
      this.model_cpu += .1 * (elapsed - this.model_cpu);
    }
  }
  private async decideAction(payload: Payload): Promise<Payload> {
    const sv = this.sv,
      sky = sv.sky,
      t = parse_utc(payload.now_utc),
      resyncs: Payload[] = [];
    this.modelCompleted = undefined;
    for (const message of payload.new_messages || []) {
      if (
        message === null ||
        typeof message !== "object" ||
        Array.isArray(message)
      )
        continue;
      if (message.record_type === "bulletin") {
        try {
          sky.bulletin(message, parse_utc(message.issued_at_utc));
        } catch {
          /* invalid bulletin: original behavior */
        }
      } else if (message.record_type === "state_resync") resyncs.push(message);
      else if (message.record_type === "observation_request")
        this.requests_seen++;
    }
    const last = payload.last_result;
    if (last && typeof last === "object" && !Array.isArray(last)) {
      if (last.action === "observe" && this.pending !== null)
        this._digest(last);
      else if (last.action === "report") {
        const correct = Boolean(last.correct);
        sky.report_result(correct, t);
        log(
          `report at ${payload.now_utc}: correct=${correct} delta=${last.score_delta} free_left=${sky.free_left}`,
        );
      }
    }
    this.pending = null;
    for (const message of resyncs) {
      const dropped = sv.resync(message);
      if (this.modelSession.enabled.includes("instrument-fault-report")) {
        sky.suspect = false; sky.low_count = 0; sky.low_seconds = 0; sky.tonight = [];
      }
      log(
        `state_resync: window ${JSON.stringify(message.invalidated_window)} dropped ${dropped} of our exposures`,
      );
    }
    const messages = (payload.new_messages || []).filter((m: Payload) => m && typeof m === "object");
    if (last?.action === "report" && !messages.some((m: Payload) => m.record_type === "report_result"))
      messages.push({ record_type: "report_result", issued_at_utc: payload.now_utc,
        correct: Boolean(last.correct), repaired: Boolean(last.repaired), score_delta: last.score_delta });
    const nextNight = sv.night_index(t), nightRemaining = nextNight < sv.nights.length ? Math.max(0, sv.nights[nextNight][1] - t) : 0;
    const remainingScience = sv.rate_time > 0 ? .5 * sv.rate_gain / sv.rate_time * (nightRemaining + (this.suffix[nextNight + 1] || 0)) : 0;
    await this.modelSession.update({ now_utc: payload.now_utc, messages, completed_exposure: this.modelCompleted,
      baseline_state: this.modelState(), sky: this.modelSky(), remaining_gain: remainingScience });
    if (this.modelSession.enabled.includes("instrument-fault-report")) {
      const prediction = await this.modelSession.faults(payload.now_utc, !this.faultGuard || sky.want_report(t, remainingScience));
      log("SURVEY26_FAULT_MODEL_V1", prediction);
    }
    sv.set_requests(payload.active_requests, t);
    const night = sv.night_index(t);
    if (night >= sv.nights.length)
      return { action: "wait", duration_seconds: sv.t_max };
    const [start, end] = sv.nights[night];
    if (t < start) return { action: "wait", until_utc: format_utc(start) };
    const rem = end - t,
      last_night = night === sv.nights.length - 1;
    if (
      last_night &&
      rem <= 1500 &&
      !this.final_probe_done &&
      this.knobs.final_probe > 0 &&
      sky.free_left >= 1
    ) {
      this.final_probe_done = true;
      return { action: "report" };
    }
    if (rem < 45)
      return {
        action: "wait",
        until_utc: format_utc(last_night ? end : sv.nights[night + 1][0]),
      };
    const remaining_gain =
      ((0.5 * sv.rate_gain) / sv.rate_time) * (rem + this.suffix[night + 1]);
    if (this.modelSession.enabled.includes("instrument-fault-report")
      ? this.modelSession.latestFault?.recommend_report : sky.want_report(t, remaining_gain)) {
      log(
        `report: e=${sky.e.toFixed(3)} W=${sky.W.toFixed(3)} rho=${sky.rho.toFixed(3)} low_count=${sky.low_count} at ${payload.now_utc}`,
      );
      return { action: "report" };
    }
    const boundary = (Math.floor(t / sv.slot) + 1) * sv.slot;
    if (sky.site_closed_now)
      return { action: "wait", until_utc: format_utc(Math.min(boundary, end)) };
    if (night !== this.night_seen) {
      this.night_seen = night;
      sv.update_urgency(night);
      sv.update_band_bonus();
    }
    this._pace(payload, t, night);
    let ceiling = sv.t_max;
    if (sky.closed_suspect) {
      ceiling = Math.trunc(boundary - t);
      if (ceiling < 240) ceiling += Math.trunc(sv.slot);
    }
    const calibrate =
        this.calib_left > 0 || (sv.offset.uncertain && this.calib_extra < 8),
      floor = calibrate || sky.closed_suspect ? sv.t_min : this.t_floor,
      began = cpu_seconds();
    const plan = this.planner.plan(
      t,
      night,
      Math.trunc(this.k),
      floor,
      ceiling,
      calibrate,
    );
    this.own += 0.1 * (cpu_seconds() - began - this.own);
    if (plan === null)
      return { action: "wait", until_utc: format_utc(Math.min(boundary, end)) };
    if (this.calib_left > 0) this.calib_left--;
    else if (calibrate) this.calib_extra++;
    return this._action(plan, t, night, rem);
  }
  async _action(plan: Plan, t: number, night: number, rem: number): Promise<Payload> {
    const sv = this.sv,
      site = sv.site,
      cat = sv.cat,
      sky = sv.sky;
    let duration = Math.trunc(plan.duration);
    if (sky.suspect && duration > 2 * sv.t_min)
      duration = Math.max(sv.t_min, Math.trunc(0.8 * duration));
    const teff = Math.min(duration, rem),
      centre = plan.centre,
      [, zen, north, east] = site.frame(t),
      sin_alt = centre[0] * zen[0] + centre[1] * zen[1] + centre[2] * zen[2];
    const alt = Math.asin(Math.max(-1, Math.min(1, sin_alt))) * DEG,
      az = mod(
        Math.atan2(
          centre[0] * east[0] + centre[1] * east[1] + centre[2] * east[2],
          centre[0] * north[0] + centre[1] * north[1] + centre[2] * north[2],
        ) * DEG,
        360,
      );
    const cmd_alt = Math.min(90, Math.max(0, alt - sv.offset.alt));
    let cmd_az = mod(az - sv.offset.az, 360);
    if (cmd_az >= 360) cmd_az = 0;
    const samples = [1 / 6, 0.5, 5 / 6].map((x) => t + teff * x),
      frames = samples.map((s) => site.frame(s)),
      moons = samples.map((s) => site.moon(s)),
      [rho, w_level] = sky.levels(),
      directional = sky.directional,
      n = sv.n_side;
    const assignments: Record<string, string> = {},
      items: ExposureItem[] = [],
      pairs: [number, number][] = [];
    let clear = true,
      predicted = 0;
    for (const [cell, j, phit] of plan.picks) {
      if (Object.hasOwn(assignments, String(cell))) continue;
      const [x, y, z] = [cat.ux[j], cat.uy[j], cat.uz[j]];
      let a_sum = 0;
      for (let i = 0; i < frames.length; i++) {
        const zen_k = frames[i][1];
        a_sum +=
          site.transmission(x * zen_k[0] + y * zen_k[1] + z * zen_k[2]) *
          site.lunar_factor(x, y, z, moons[i]);
      }
      const a = a_sum / 3,
        unit = (cat.flux[j] * teff * a) / sv.f0t0,
        [, zen_m, north_m, east_m] = frames[1],
        alt_j =
          Math.asin(
            Math.max(
              -1,
              Math.min(1, x * zen_m[0] + y * zen_m[1] + z * zen_m[2]),
            ),
          ) * DEG;
      const az_j = mod(
          Math.atan2(
            x * east_m[0] + y * east_m[1] + z * east_m[2],
            x * north_m[0] + y * north_m[1] + z * north_m[2],
          ) * DEG,
          360,
        ),
        d = directional ? sky.dir_factor(alt_j, az_j) : 1;
      if (d < 0.999) clear = false;
      const row = Math.floor(cell / n),
        col = cell % n;
      items.push([
        j,
        row,
        col,
        unit,
        a,
        d,
        alt_j,
        az_j,
        phit >= 0.999,
        cat.weight[j],
        x * north[0] + y * north[1] + z * north[2],
        x * east[0] + y * east[1] + z * east[2],
        x * zen[0] + y * zen[1] + z * zen[2],
      ]);
      assignments[String(cell)] = cat.ids[j];
      const g = unit * rho * d;
      pairs.push([cat.weight[j] * Math.min(g, 1) + 1e-6, w_level * a]);
      const expected =
        cat.weight[j] *
          Math.min(g, 1) *
          sky.expected_multiplier(w_level * a, t) -
        sv.best[j];
      if (expected > 0) predicted += expected;
    }
    const program = sky.choose_program(pairs, t);
    if (this.modelSession.enabled.includes("conditions-reliability")) {
      const began = this.models.worker.cpuSeconds;
      const candidates = [900, 1800, 3600].flatMap(lead => items.map(item => ({
        candidate_id: `${cat.ids[item[0]]}:${lead}`, ra_deg: cat.ra[item[0]], dec_deg: cat.dec[item[0]],
        start_utc: format_utc(t + lead), duration_seconds: duration,
      })));
      const predictions = await this.modelSession.conditions(format_utc(t), candidates);
      const elapsed = this.models.worker.cpuSeconds - began;
      log("SURVEY26_MODELS_V1", { now_utc: format_utc(t), candidates: candidates.length, cpu_seconds: elapsed,
        models: [{ model_id: predictions.model_id, model_version: predictions.model_version,
          eligible: predictions.predictions.filter(r => r.eligible).length, final_test_passed: predictions.final_test_passed }] });
      if (process.env.BASE_MODELS_LOG === "full") log("SURVEY26_MODEL_PREDICTIONS_V1", predictions);
    }
    this.pending = {
      t0: t,
      teff,
      program,
      cmd_alt,
      cmd_az,
      items,
      night,
      clear_dirs: clear,
      notices: [],
      predicted,
    };
    return {
      action: "observe",
      pointing: { alt_deg: cmd_alt, az_deg: cmd_az },
      assignments,
      duration_seconds: duration,
      program,
    };
  }
  modelState(): Payload {
    const sky = this.sv.sky, [rho, weather] = sky.levels();
    return { weather, throughput: rho, efficiency: sky.e, has_measurement: sky.measured,
      last_measurement_utc: sky.tonight.length ? format_utc(sky.tonight[sky.tonight.length - 1][0]) : null,
      suspected_fault: sky.suspect, suspected_closure: sky.closed_suspect || sky.site_closed_now, quake_notice: sky.quake_now,
      terrain: sky.terrain.map(p => ({ centre: p.centre, sure: p.sure, top: p.top, floor: p.floor, box: p.box })),
      directional_notices: sky.dir_now };
  }
  modelSky(): Payload {
    const sky = this.sv.sky;
    return { b_dark: sky.b_dark, b_bright: sky.b_bright, W: sky.W, e: sky.e, e_ref: sky.e_ref,
      suspect: sky.suspect, low_count: sky.low_count, low_seconds: sky.low_seconds, closed_suspect: sky.closed_suspect,
      probe: sky.probe, free_left: sky.free_left, reports_false: sky.reports_false, cooldown_until: sky.cooldown_until };
  }
  async close(): Promise<void> { await this.modelSession.close(); }
  status(payload: Payload): string {
    const sv = this.sv,
      sky = sv.sky,
      clock = payload.wallclock || {};
    return `${payload.now_utc} obs=${sv.observes} total=${payload.running_total} req_left=${sv.required_left} rho=${sky.rho.toFixed(3)} W=${sky.W.toFixed(3)} e=${sky.e.toFixed(2)} price=${sv.price.toFixed(5)} avgT=${this.avg_t.toFixed(0)} k=${this.k.toFixed(1)} own=${(this.own * 1000).toFixed(1)}ms eng=${(this.engine * 1000).toFixed(1)}ms floor=${this.t_floor} miss=${this.misses}/${this.assigned} gain/pred=${(this.realised_gain / Math.max(this.predicted_gain, 1e-9)).toFixed(3)} capped=${this.planner.stat_negative}/${this.planner.stat_plans} cpu_left=${clock.remaining_real_cpu_seconds} wall_left=${clock.wall_remaining_seconds}`;
  }
}
export function safe_wait(_payload: Payload): Payload {
  return { action: "wait", duration_seconds: 900 };
}
/** Retains full incoming lines, including the last line without a newline. */
export async function* raw_lines(
  input: NodeJS.ReadableStream,
): AsyncGenerator<string> {
  const decoder = new StringDecoder("utf8");
  let pending = "";
  for await (const chunk of input) {
    pending += typeof chunk === "string" ? chunk : decoder.write(chunk);
    let end: number;
    while ((end = pending.indexOf("\n")) >= 0) {
      yield pending.slice(0, end + 1);
      pending = pending.slice(end + 1);
    }
  }
  pending += decoder.end();
  if (pending) yield pending;
}
export async function main(): Promise<number> {
  let agent: Agent | null = null,
    received_sequence = 0;
  try {
  for await (const line of raw_lines(process.stdin)) {
    received_sequence++;
    if (
      !process.stderr.write(
        "SURVEY26_INPUT_V1 " +
          asciiJson({ received_sequence, raw: line }) +
          "\n",
      )
    )
      await once(process.stderr, "drain");
    if (!line.trim()) continue;
    let message: Payload;
    try {
      message = JSON.parse(line);
    } catch {
      continue;
    }
    if (!message || typeof message !== "object" || Array.isArray(message))
      continue;
    const kind = message.message_type;
    if (kind === "initialize") {
      try {
        await agent?.close();
        agent = new Agent(message.payload);
      } catch (error) {
        log(
          "initialize failed:\n" +
            (error instanceof Error ? error.stack : String(error)),
        );
        agent = null;
      }
      continue;
    }
    if (kind === "finish") {
      if (agent !== null) log("finish:", message.payload ?? {});
      break;
    }
    if (kind !== "decision_request") continue;
    const arrived = monotonic(),
      payload = message.payload || {};
    let action: Payload | null = null;
    if (agent !== null) {
      if (agent.reply_at !== null)
        agent.engine += 0.05 * (arrived - agent.reply_at - agent.engine);
      try {
        action = await agent.decide(payload);
      } catch (error) {
        log(
          "decide failed:\n" +
            (error instanceof Error ? error.stack : String(error)),
        );
        agent.pending = null;
      }
      agent.decisions++;
      if (agent.decisions % Math.trunc(agent.knobs.log_every) === 0)
        log(agent.status(payload));
    }
    action ??= safe_wait(payload);
    const response = {
      protocol_version: message.protocol_version ?? PROTOCOL,
      message_type: "decision_response",
      decision_sequence: message.decision_sequence ?? null,
      ...action,
    };
    await new Promise<void>((resolve, reject) =>
      process.stdout.write(JSON.stringify(response) + "\n", (error) =>
        error ? reject(error) : resolve(),
      ),
    );
    if (agent !== null) agent.reply_at = monotonic();
  }
  } finally { await agent?.close(); }
  return 0;
}
if (require.main === module)
  main().catch((error) => {
    log(error instanceof Error ? error.stack : String(error));
    process.exitCode = 1;
  });
