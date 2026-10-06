#!/usr/bin/env node
/**
 * Agent Observer v4 reference agent ("pro"), TypeScript port of examples/python-pro.
 * Node built-ins only, participant-agent-protocol-v4.
 *
 * One JSON object per line on stdin, one per line on stdout, logs on stderr.
 *
 * What it does (details in README.md and planner.ts):
 *
 * 1. Planning: every decision picks pointing, fibre assignment, duration and program together, maximising
 *    expected gain minus a price for telescope time (gain - lambda * T). Required targets and observation
 *    requests enter as probability-weighted bonuses.
 * 2. Program: the band level is fitted to saturated hits, which show the program multiplier exactly.
 * 3. Instrument faults: E = (quality level) / (band level). Weather moves both, a fault only the first;
 *    when E stays low the agent reports. Free false reports are spent early; each low episode is probed
 *    once; paid probes need two low nights.
 * 4. Pace: the search level adapts to the measured CPU cost per decision so a 4-month card fits the clock.
 * 5. Model (advisor.ts): at every night start a night plan (forecast + bulletin -> bad night, sectors to avoid)
 *    and a fault review (own quality table -> how likely a fault is, which gates paid reports); before a paid
 *    report the model confirms or vetoes. Calls run in the background; without an API key the agent exits,
 *    except when the platform sets OBSERVER_MODEL_DISABLED=1 (an evaluation without a model): then rules only.
 */
import * as path from "node:path";
import * as readline from "node:readline";
import { Advisor, FaultReview, NightPlan, Notice } from "./advisor";
import { Json, LLMClient, apiKey, loadDotenv } from "./llmClient";
import { Planner, env } from "./planner";
import { formatUtc, parseUtc, pyround, pysum, upperMedian } from "./skymath";

const BAD_KINDS = new Set(["rain", "storm", "overcast", "haze"]);
const PROTOCOL = "participant-agent-protocol-v4";

// --- fault reporting (see faultVerdict) ---
const E_LOW_FREE = env("E_LOW_FREE", 0.9); // free probe: E below this in 3 of the last 4 hours
const E_LOW_FREE2 = env("E_LOW_FREE2", 0.9); // ... threshold while both free probes are left
const E_FREE_HOURS = env("E_FREE_HOURS", 4);
const E_LOW = env("E_LOW", 0.85); // paid probe: low hours must span two nights ...
const E_PAID_LOW = env("E_PAID_LOW", 0.75); // ... the median of the last 12 hourly E below this ...
const E_PAID_HOURS = env("E_PAID_HOURS", 12);
const E_RECOVER = env("E_RECOVER", 0.95); // after a false probe, wait until E is back above this
const MAX_PAID_FALSE = env("MAX_PAID", 6);
const PERSIST_NIGHTS = env("PERSIST_NIGHTS", 3);
const E_PAID_STEP = env("E_PAID_STEP", 0.05); // ... minus this per paid false probe so far
const MAX_FALSE_REPORTS = 8;
// The participant guide: an earthquake (announced in the bulletin) lowers instrument efficiency, the loss fades
// night by night, and a report does not repair it. So E drops right after an earthquake are not reportable, and
// while its effect may last only a new step down in E (a fresh drop from the preceding hours) is fault evidence.
const QUAKE_HOLD_HOURS = env("QUAKE_HOLD_HOURS", 12.0); // no probes this long after an earthquake notice appears
const QUAKE_STEP = env("QUAKE_STEP", 0.8); // step: median E of the last 3 rows < this x the 9 rows before
const QUAKE_TAIL_HOURS = env("QUAKE_TAIL_HOURS", 24.0); // the earthquake period lasts this long after its last notice
const PAID_SPACING_HOURS = 20.0;
const MIN_REPORT_SPACING_HOURS = 2.0;
// --- pace ---
const PACE_SAFETY = env("PACE_SAFETY", 0.75); // spend at most this share of the remaining budget
// --- model ---
const MODEL_WAIT_MAX = env("MODEL_WAIT_MAX", 20.0); // longest wait for the night's model answers (s)
const MODEL_FAULT_HIGH = env("MODEL_FAULT_HIGH", 0.6); // fault review at or above this: report more readily tonight
const MODEL_FAULT_LOW = env("MODEL_FAULT_LOW", 0.15); // ... at or below this: paid reports need the strongest evidence
const SCALE_STEP = env("SCALE_STEP", 0.7); // with a likely fault: report when scale stays below this x ref
const MODEL_FREE_PROBE = env("MODEL_FREE_PROBE", 0); // 1: a high fault review may also spend a free probe on a low scale
const FIXED_LEVEL = env("FIXED_LEVEL", -1); // development only: pin the search level (deterministic runs)

type Action = Record<string, unknown> & { action: string };
type ERow = [number, number, number]; // hour, night, median E
type Payload = any; // protocol JSON (participant guide), read defensively

function log(text: string): void {
  process.stderr.write(text + "\n");
}

/** Real seconds (monotonic) and own process CPU seconds (user + system, all threads). */
const monotonic = (): number => performance.now() / 1000;
const processTime = (): number => {
  const usage = process.cpuUsage();
  return (usage.user + usage.system) / 1e6;
};

/** The advisor's interface without a model (OBSERVER_MODEL_DISABLED=1): every rule default stands. */
class RulesOnly {
  nightDate: string | null = null;

  async startNight(nightDate: string): Promise<[NightPlan | null, FaultReview | null]> {
    this.nightDate = nightDate;
    return [null, null];
  }

  poll(): [NightPlan | null, FaultReview | null] {
    return [null, null];
  }

  async confirmReport(): Promise<boolean | null> {
    return null;
  }
}

/** The platform sets OBSERVER_MODEL_DISABLED=1 for an evaluation started with 「本次不提供模型」 / --no-model. */
function modelDisabled(): boolean {
  return process.env.OBSERVER_MODEL_DISABLED === "1";
}

class ObserverAgent {
  private readonly planner: Planner;
  private readonly client: LLMClient | null;
  private readonly advisor: Advisor | RulesOnly;
  private modelWait = 0.0; // wall seconds spent waiting for the model (not planning cost)
  private faultLikely: number | null = null; // tonight's model estimate that an instrument fault is active
  private readonly scaleHours = new Map<number, number[]>(); // hour -> [planner.scale samples] (for the model's fault table)
  private readonly start: number;
  private forecastNotices: Notice[] = [];
  private nightSeen: number | null = null;
  observes = 0;
  // fault reporting state
  private readonly freeAllowance: number;
  reports = 0;
  private correctReports = 0;
  private falseReports = 0;
  private falseSinceCorrect = 0;
  private paidFalse = 0;
  private lastReportHours = -1e9;
  private refFromHours = -1e9;
  private episodeBlocked = false;
  private blockedAtHour = -1;
  private quakeOn = false;
  private quakeOnsetHours = -1e9;
  private quakeLastHours = -1e9;
  private loggedHour: number | null = null;
  // pace state
  private readonly costEma = [0.0, 0.0, 0.0, 0.0]; // CPU seconds per observe decision at each search level
  private readonly wallEma = [0.0, 0.0, 0.0, 0.0]; // real seconds per observe decision (own turn, model waits excluded)
  private turnEnd: number | null = null;
  private decisions = 0;
  private engineEma: number | null = null;
  private simStepEma: number | null = null;
  private lastNow: number | null = null;

  constructor(init: Payload, readonly rulesOnly = false) {
    const started = monotonic();
    this.planner = new Planner(init, log);
    this.client = rulesOnly ? null : new LLMClient(log);
    this.advisor = this.client === null ? new RulesOnly() : new Advisor(this.client, log);
    this.start = parseUtc(init.survey.start_utc);
    const reporting = init.scoring.reporting ?? {};
    this.freeAllowance = Math.trunc(Number(reporting.false_report_free_allowance ?? 0));
    log(
      `pro: ${this.planner.ids.length} targets, ${this.planner.required.filter(Boolean).length} required, ` +
        `${this.planner.nights.length} nights; init ${(monotonic() - started).toFixed(2)}s; model ${this.client ? this.client.model : "none (rules only)"}`,
    );
  }

  // --- decision loop ------------------------------------------------------------------------------

  async respond(payload: Payload): Promise<Action> {
    const started = monotonic();
    const cpuStarted = processTime();
    if (this.turnEnd !== null) {
      // engine time between our turns (charged only by the old real-time clock)
      const gap = started - this.turnEnd;
      if (gap >= 0.0 && gap < 5.0) this.engineEma = this.engineEma === null ? gap : 0.95 * this.engineEma + 0.05 * gap;
    }
    const modelBefore = this.modelWait;
    const level = this.planner.fastLevel;
    const action = await this.respondInner(payload);
    // the platform charges CPU time inside our turns; waiting for the model is free of CPU, so keep it
    // out of the real-time estimate as well
    const cpu = processTime() - cpuStarted;
    const wall = monotonic() - started - (this.modelWait - modelBefore);
    if (action.action === "observe" && level < 4) {
      for (const [ema, cost] of [
        [this.costEma, cpu],
        [this.wallEma, wall],
      ] as [number[], number][]) {
        const c = ema[level] as number;
        ema[level] = c === 0.0 ? cost : 0.9 * c + 0.1 * cost;
        for (let k = level + 1; k < 4; k++) {
          // cheaper levels not measured yet: a third of the level above
          if (ema[k] === 0.0 || (ema[k] as number) > (ema[k - 1] as number)) ema[k] = (ema[k - 1] as number) / 3.0;
        }
      }
    }
    this.turnEnd = monotonic();
    return action;
  }

  private async respondInner(payload: Payload): Promise<Action> {
    const now = parseUtc(payload.now_utc);
    const planner = this.planner;
    if (this.lastNow !== null && planner.currentNight(now) !== null) {
      const step = now - this.lastNow;
      if (step > 0 && step <= 3600) this.simStepEma = this.simStepEma === null ? step : 0.95 * this.simStepEma + 0.05 * step;
    }
    this.lastNow = now;
    const hours = (now - this.start) / 3600.0;
    const messages = payload.new_messages ?? [];
    for (const message of messages) {
      if (message?.record_type === "forecast") this.forecastNotices = message.notices ?? [];
    }
    const last = payload.last_result ?? {};
    if (last.action === "report") this.onReportResult(last, hours);
    planner.onMessages(messages, payload.latest_bulletin);
    const quake = planner.notices.some(([kind]) => kind === "earthquake");
    if (quake && !this.quakeOn) {
      this.quakeOnsetHours = hours;
      log(`pro: earthquake notice at ${payload.now_utc}`);
    }
    this.quakeOn = quake;
    if (quake) this.quakeLastHours = hours;
    planner.onRequests(payload.active_requests ?? []);
    planner.onResult(payload.last_result, now, hours);
    this.pace(payload, now);

    const night = planner.currentNight(now);
    if (night === null) {
      const nxt = planner.nextNightStart(now);
      if (nxt === null) return { action: "finish", reason: "no observing night left" };
      return { action: "wait", until_utc: formatUtc(nxt), reason: "daytime: sleep until the next night" };
    }
    const [nightIndex, nightStart, nightEnd] = night;
    if (this.nightSeen !== nightIndex) {
      this.nightSeen = nightIndex;
      await this.nightAdvice(nightStart, payload, hours);
    } else {
      this.applyAdvice(...this.advisor.poll());
    }
    const hour = Math.trunc(hours);
    let samples = this.scaleHours.get(hour);
    if (samples === undefined) this.scaleHours.set(hour, (samples = []));
    samples.push(planner.scale);
    if (nightEnd - now < planner.minExposure) {
      const nxt = planner.nextNightStart(now);
      if (nxt === null) return { action: "finish", reason: "survey over" };
      return { action: "wait", until_utc: formatUtc(nxt), reason: "night ending" };
    }
    if (planner.siteClosed()) {
      return { action: "wait", duration_seconds: this.toNextSlot(now, nightStart), reason: "bulletin: rain/storm over the whole sky" };
    }
    const report = await this.maybeReport(hours, payload);
    if (report !== null) return report;
    const action = planner.plan(now, nightEnd, nightIndex, hours);
    if (action === null) {
      return { action: "wait", duration_seconds: this.toNextSlot(now, nightStart), reason: "nothing useful is up" };
    }
    this.observes += 1;
    action.reason = `${Object.keys(action.assignments).length} fibres, program ${action.program}`;
    return action as unknown as Action;
  }

  private toNextSlot(now: number, nightStart: number): number {
    const slot = this.planner.slotSeconds;
    const into = (now - nightStart) % slot;
    return Math.trunc(Math.max(60, Math.min(3600, slot - into)));
  }

  // --- pace ---------------------------------------------------------------------------------------

  /** [CPU seconds left, real seconds left, fair clock?] from the request's wallclock block.
   *
   *  Fair clock (current platform): the budget is normalized CPU time inside our turns, and
   *  remaining_real_cpu_seconds converts it to this machine's CPU seconds; a separate real-time cap
   *  (wall_remaining_seconds) only guards against runaway runs. Older runners count real time only. */
  private clock(payload: Payload): [number, number, boolean] {
    const wall = payload.wallclock ?? {};
    if (wall.remaining_real_cpu_seconds !== undefined) {
      return [Number(wall.remaining_real_cpu_seconds), Number(wall.wall_remaining_seconds ?? 1e9), true];
    }
    const remaining = Number(wall.remaining_seconds ?? 1e9);
    return [remaining, remaining, false];
  }

  private decisionsLeft(now: number): number {
    const nightSeconds = pysum(this.planner.nights.filter(([, end]) => end > now).map(([start, end]) => Math.max(0.0, end - Math.max(start, now))));
    return Math.max(1.0, nightSeconds / (this.simStepEma || 900.0)); // daytime waits cost nothing
  }

  /** Pick the search level from the measured cost per decision and the decisions still to come. */
  private pace(payload: Payload, now: number): void {
    const [cpuLeft, wallLeft, fair] = this.clock(payload);
    const decisionsLeft = this.decisionsLeft(now);
    const engine = this.engineEma || 0.0;
    const cpuBudget = fair ? (PACE_SAFETY * cpuLeft) / decisionsLeft : 1e9;
    const wallBudget = (PACE_SAFETY * wallLeft) / decisionsLeft - engine;
    // estimates of levels not used for a while decay, so the agent climbs back up and re-measures them
    this.decisions += 1;
    if (this.decisions % 50 === 0) {
      for (let k = 0; k < 4; k++) {
        if (k !== this.planner.fastLevel) {
          this.costEma[k] = (this.costEma[k] as number) * 0.85;
          this.wallEma[k] = (this.wallEma[k] as number) * 0.85;
        }
      }
    }
    if (FIXED_LEVEL >= 0) {
      this.planner.fastLevel = FIXED_LEVEL;
      return;
    }
    let level = 0;
    while (level < 3 && ((this.costEma[level] as number) > cpuBudget || (this.wallEma[level] as number) > wallBudget)) level += 1;
    if (Math.min(cpuLeft, wallLeft) < 15.0) level = 4;
    if (level !== this.planner.fastLevel) {
      log(
        `pro: pace level ${level} (cpu budget ${(Math.min(cpuBudget, 99) * 1000).toFixed(0)} ms, wall budget ${(wallBudget * 1000).toFixed(0)} ms, ` +
          `cpu costs [${this.costEma.map((c) => Math.round(c * 1000)).join(", ")}] ms, ${decisionsLeft.toFixed(0)} decisions left)`,
      );
      this.planner.fastLevel = level;
    }
  }

  // --- model stages (advisor.ts): night plan and fault review at every night start --------------------

  /** Night start: rule defaults first, then the two model calls (night plan, fault review). */
  private async nightAdvice(nightStart: number, payload: Payload, hours: number): Promise<void> {
    const nightDate = new Date((nightStart - 12 * 3600) * 1000).toISOString().slice(0, 10);
    const tonight = this.forecastNotices.filter((n) => (n.nights ?? []).includes(nightDate));
    const bulletin: Notice[] = payload.latest_bulletin?.notices ?? [];
    // rule defaults, kept when the model gives no valid answer
    this.planner.badForecast = tonight.some((n) => n.direction === "ALL" && BAD_KINDS.has(n.event_kind as string));
    this.planner.extraAvoid = new Set();
    this.faultLikely = null;
    const left = this.clock(payload)[1];
    const started = monotonic();
    const answers = await this.advisor.startNight(nightDate, tonight, bulletin, this.faultTable(hours), left, this.modelWaitBudget(payload));
    this.modelWait += monotonic() - started;
    this.applyAdvice(...answers);
  }

  /** How long a night start may wait for the model. Waiting costs no CPU budget, only real time: use half
   *  of the real time the planner and the engine will not need, spread over the nights left. */
  private modelWaitBudget(payload: Payload): number {
    const wallLeft = this.clock(payload)[1];
    const now = this.lastNow as number;
    const nightsLeft = Math.max(1, this.planner.nights.filter(([, end]) => end > now).length);
    const perDecision = Math.max(this.wallEma[Math.min(this.planner.fastLevel, 3)] as number, 0.05) + (this.engineEma || 0.02);
    const spare = wallLeft - 1.5 * this.decisionsLeft(now) * perDecision - 60.0;
    return Math.max(0.0, Math.min(MODEL_WAIT_MAX, (0.5 * spare) / nightsLeft));
  }

  private applyAdvice(plan: NightPlan | null, fault: FaultReview | null): void {
    if (plan !== null) {
      this.planner.badForecast = plan.bad_night;
      this.planner.extraAvoid = new Set(plan.avoid_directions);
      log(`llm night plan ${this.advisor.nightDate}: bad_night=${plan.bad_night} avoid=${JSON.stringify(plan.avoid_directions)} (${plan.reason})`);
    }
    if (fault !== null) {
      this.faultLikely = fault.fault_likely;
      log(`llm fault review ${this.advisor.nightDate}: fault_likely=${fault.fault_likely.toFixed(2)} (${fault.reason})`);
    }
  }

  /** Usual clear-sky scale since the last repair: 75th percentile of the hourly medians. */
  private scaleRef(): number {
    const values: number[] = [];
    for (const [h, v] of this.scaleHours) if (h >= this.refFromHours && v.length > 0) values.push(upperMedian(v));
    values.sort((a, b) => a - b);
    return values.length >= 4 ? (values[Math.floor((3 * values.length) / 4)] as number) : 1.0;
  }

  /** E rows since the last repair: [hour, night, median E]. */
  private eRows(): ERow[] {
    return this.planner.eHours.filter(([hour]) => hour >= this.refFromHours).map(([hour, night, v]) => [hour, night, upperMedian(v)] as ERow);
  }

  /** The evidence the fault review reads: the last ~30 observed hours. */
  private faultTable(hours: number): Json {
    const eByHour = new Map<number, number>();
    for (const [hour, , e] of this.eRows()) eByHour.set(hour, e);
    const rows: [string, number | null, number][] = [];
    for (const hour of [...this.scaleHours.keys()].sort((a, b) => a - b).slice(-30)) {
      if (hour < this.refFromHours) continue;
      const stamp = new Date((this.start + hour * 3600) * 1000).toISOString();
      const label = `${stamp.slice(5, 10)}T${stamp.slice(11, 13)}`;
      const e = eByHour.get(hour);
      rows.push([label, e === undefined ? null : pyround(e, 2), pyround(upperMedian(this.scaleHours.get(hour) as number[]), 2)]);
    }
    const notices = [...new Set(this.planner.notices.map(([kind, direction]) => `${kind} ${direction}`))].sort();
    return {
      columns: ["utc_hour", "E", "scale"],
      rows,
      ref: pyround(this.scaleRef(), 2),
      notices_now: notices,
      hours_since_earthquake_notice_began: this.quakeOnsetHours < -1e8 ? null : pyround(hours - this.quakeOnsetHours, 1),
      free_false_reports_left: Math.max(0, this.freeLeft()),
      paid_false_reports_so_far: this.paidFalse,
      correct_reports_so_far: this.correctReports,
      hours_since_last_report: this.reports === 0 ? null : pyround(hours - this.lastReportHours, 1),
    };
  }

  // --- instrument faults ---------------------------------------------------------------------------

  /** Report (probe) when the quality level stays below what the program bands allow.
   *
   *  A report costs no time, its answer arrives at once, and the first false reports after each correct
   *  one are free: spend free probes readily, paid ones only on strong, lasting evidence. */
  private async maybeReport(hours: number, payload: Payload): Promise<Action | null> {
    if (this.falseReports >= MAX_FALSE_REPORTS || hours - this.lastReportHours < MIN_REPORT_SPACING_HOURS) return null;
    if (hours - this.quakeOnsetHours < QUAKE_HOLD_HOURS) return null; // the earthquake explains the drop; a report would not repair it
    if (this.faultVerdict(hours, payload) && (await this.modelAgrees(hours, payload))) {
      this.lastReportHours = hours;
      this.reports += 1;
      log(`pro: report at ${payload.now_utc} (quality below what the program bands allow), free left ${this.freeLeft()}`);
      return { action: "report", reason: "quality level below what the program bands allow" };
    }
    return null;
  }

  /** Hourly E = quality level / band level (planner.eHours). 1 = consistent; a fault keeps E low. */
  private faultVerdict(hours: number, payload: Payload): boolean {
    const rows = this.eRows();
    const hourNow = Math.trunc(hours);
    const lastRow = rows[rows.length - 1];
    if (lastRow !== undefined && hourNow !== this.loggedHour) {
      this.loggedHour = hourNow;
      log(`pro: E ${payload.now_utc} ${lastRow[2].toFixed(2)} scale ${this.planner.scale.toFixed(3)} band ${(this.planner.bandLevel || 0).toFixed(3)}`);
    }
    if (rows.length < E_FREE_HOURS || (lastRow as ERow)[0] < hourNow - 1) return false;
    const es = (part: ERow[]) => part.map(([, , e]) => e);
    if (QUAKE_STEP > 0 && hours - this.quakeLastHours < QUAKE_TAIL_HOURS) {
      if (rows.length < 8) return false;
      const last3 = es(rows.slice(-3)).sort((a, b) => a - b)[1] as number;
      const prev = es(rows.slice(-12, -3)).sort((a, b) => a - b);
      const step = rows[rows.length - 3] as ERow;
      if (!(last3 < QUAKE_STEP * (prev[Math.floor(prev.length / 2)] as number) && step[0] >= hourNow - 4)) return false;
      if (this.episodeBlocked && step[0] <= this.blockedAtHour) return false; // the step that was already probed, not a new one
      this.episodeBlocked = false; // a new step is a new episode
    }
    if (this.episodeBlocked) {
      // this low episode was probed already and was not a fault: wait for a recovery first
      const last4 = rows.slice(-4);
      if (last4.filter(([, , e]) => e >= E_RECOVER).length >= 3 && (lastRow as ERow)[0] > this.blockedAtHour) {
        this.episodeBlocked = false;
        log(`pro: quality recovered at ${payload.now_utc}; probing re-armed`);
      } else {
        return false;
      }
    }
    const likely = this.faultLikely;
    if (MODEL_FREE_PROBE && likely !== null && likely >= MODEL_FAULT_HIGH && this.freeLeft() > 0 && this.scaleStep()) {
      // off by default: on the practice cards it spent free probes on unannounced weather
      log(`pro: model-flagged fault (likely ${likely.toFixed(2)}) and scale below ${SCALE_STEP} x ref`);
      return true;
    }
    if (this.freeLeft() > 0) {
      const last = rows.slice(-E_FREE_HOURS);
      const threshold = this.freeLeft() >= 2 ? E_LOW_FREE2 : E_LOW_FREE;
      const low = last.filter(([, , e]) => e < threshold).length;
      const first = last[0] as ERow;
      const final = last[last.length - 1] as ERow;
      return low >= E_FREE_HOURS - 1 && final[2] < threshold && final[0] - first[0] <= E_FREE_HOURS + 3;
    }
    if (this.paidFalse >= MAX_PAID_FALSE || hours - this.lastReportHours < PAID_SPACING_HOURS) return false;
    const last = rows.slice(-E_PAID_HOURS);
    const values = es(last).sort((a, b) => a - b);
    const nights = new Set(last.filter(([, , e]) => e < E_LOW).map(([, night]) => night));
    // a fault never goes away on its own: three low nights in a row justify a probe whatever the bar
    const byNight = new Map<number, number[]>();
    for (const [, night, e] of rows) {
      let list = byNight.get(night);
      if (list === undefined) byNight.set(night, (list = []));
      list.push(e);
    }
    const nightsSeq = [...byNight.keys()].sort((a, b) => a - b).slice(-PERSIST_NIGHTS);
    if (
      nightsSeq.length === PERSIST_NIGHTS &&
      (nightsSeq[nightsSeq.length - 1] as number) - (nightsSeq[0] as number) <= PERSIST_NIGHTS &&
      nightsSeq.every((n) => {
        const list = byNight.get(n) as number[];
        return list.length >= 3 && upperMedian(list) < E_LOW;
      }) &&
      hours - this.lastReportHours >= 40.0
    ) {
      return true;
    }
    if (likely !== null && likely <= MODEL_FAULT_LOW) return false; // the fault review sees weather, not a fault: only the persistence rule above may report
    // each paid false probe raises the bar for the next one
    const paidLow = E_PAID_LOW - E_PAID_STEP * this.paidFalse;
    return (
      last.length === E_PAID_HOURS &&
      (values[Math.floor(values.length / 2)] as number) < paidLow &&
      nights.size >= 2 &&
      last.slice(-3).every(([, , e]) => e < E_LOW)
    );
  }

  /** The last 3 observed hours all sit below SCALE_STEP x the usual clear-sky scale. */
  private scaleStep(): boolean {
    const recent = [...this.scaleHours.entries()]
      .sort((a, b) => a[0] - b[0])
      .filter(([h, v]) => h >= this.refFromHours && v.length > 0)
      .map(([, v]) => upperMedian(v))
      .slice(-3);
    return recent.length === 3 && Math.max(...recent) < SCALE_STEP * this.scaleRef();
  }

  /** Paid probes only: the model looks at the evidence first and may veto. Free probes cost nothing, so they
   *  never wait for it. No answer in time: the rule's decision stands. */
  private async modelAgrees(hours: number, payload: Payload): Promise<boolean> {
    if (this.freeLeft() > 0) return true;
    const rows = this.eRows().slice(-24);
    const evidence = {
      hourly_E_last_24h: rows.map(([, , e]) => pyround(e, 2)),
      fault_table: this.faultTable(hours),
      paid_false_reports_so_far: this.paidFalse,
      correct_reports_so_far: this.correctReports,
    };
    const started = monotonic();
    const left = this.clock(payload)[1];
    const verdict = await this.advisor.confirmReport(evidence, left, Math.min(30.0, 2.0 * this.modelWaitBudget(payload)));
    this.modelWait += monotonic() - started;
    if (verdict === false) {
      log(`pro: model vetoed a paid report at ${payload.now_utc}`);
      this.lastReportHours = hours;
      return false;
    }
    return true;
  }

  freeLeft(): number {
    return this.freeAllowance - this.falseSinceCorrect;
  }

  private onReportResult(result: Payload, hours: number): void {
    if (result.correct) {
      log(`pro: report correct, fault repaired (delta ${result.score_delta})`);
      this.correctReports += 1;
      this.falseSinceCorrect = 0;
      this.planner.forgetQualityHistory();
      this.refFromHours = hours;
    } else {
      this.falseSinceCorrect += 1;
      this.falseReports += 1;
      this.episodeBlocked = true;
      this.blockedAtHour = Math.trunc(hours);
      if (this.falseSinceCorrect > this.freeAllowance) this.paidFalse += 1;
      log(`pro: report false (delta ${result.score_delta}); free left ${this.freeLeft()}`);
    }
  }
}

async function main(): Promise<number> {
  loadDotenv(path.join(__dirname, "..", ".env"));
  const rulesOnly = modelDisabled();
  if (rulesOnly) {
    log("pro: OBSERVER_MODEL_DISABLED=1, running rules only (no model calls)");
  } else if (!apiKey()) {
    log("missing API key: set OPENAI_API_KEY");
    return 2;
  }
  let agent: ObserverAgent | null = null;
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    const kind = message.message_type;
    if (message.protocol_version !== PROTOCOL) log(`pro: unexpected protocol ${JSON.stringify(message.protocol_version)}`);
    if (kind === "initialize") {
      agent = new ObserverAgent(message.payload, rulesOnly);
    } else if (kind === "decision_request") {
      let action: Action;
      try {
        if (agent === null) throw new Error("decision_request before initialize");
        action = await agent.respond(message.payload);
      } catch (exc) {
        // never crash the run: wait one slot instead
        log(`pro: error ${exc instanceof Error ? `${exc.name}: ${exc.message}` : String(exc)}; waiting one slot`);
        action = { action: "wait", duration_seconds: 900, reason: "internal error" };
      }
      if (!("decision_source" in action)) action.decision_source = rulesOnly ? "rules" : "llm-advised";
      process.stdout.write(
        JSON.stringify({ protocol_version: PROTOCOL, message_type: "decision_response", decision_sequence: message.decision_sequence, ...action }) + "\n",
      );
    } else if (kind === "finish") {
      const payload = message.payload ?? {};
      log(`pro finished: termination_reason=${payload.termination_reason} observes=${agent?.observes ?? 0} reports=${agent?.reports ?? 0}`);
    }
  }
  return 0;
}

main().then(
  (code) => process.stdout.write("", () => process.exit(code)), // background model calls must not keep the process alive
  (exc) => {
    log(`pro: fatal ${exc instanceof Error ? exc.stack : String(exc)}`);
    process.exit(1);
  },
);
