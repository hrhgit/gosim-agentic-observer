/**
 * The model-driven stages of the pro agent. Two calls start at the beginning of every night:
 *
 * 1. night_plan   (natural-language understanding + plan adaptation): reads tonight's forecast and the
 *    current bulletin and decides whether tonight is a bad night for faint must-observe targets and which
 *    compass sectors to keep away from. The planner uses both answers for the whole night.
 * 2. fault_review (data parsing + action decision): reads the agent's own hour-by-hour quality table of the
 *    last nights and judges how likely an unannounced instrument fault is. The answer sets how readily the
 *    agent reports (probes) a fault tonight.
 *
 * A third, occasional call confirms a paid fault report before it is sent.
 *
 * Calls run in the background (llmClient.Call); the agent waits for them only as long as the wall clock
 * allows and keeps planning otherwise. Every answer is validated; a missing or invalid answer leaves the
 * rule-based value in place for that night. Port of python-pro/advisor.py (same prompts).
 */
import { Call, Json, LLMClient } from "./llmClient";

const DIRECTIONS = new Set(["N", "NE", "E", "SE", "S", "SW", "W", "NW"]);
const WEATHER_KINDS = new Set(["rain", "storm", "overcast", "haze", "cold_snap"]);

export interface Notice {
  event_kind?: string;
  direction?: string;
  nights?: string[];
  [key: string]: unknown;
}

export interface NightPlan {
  bad_night: boolean;
  avoid_directions: string[];
  reason: string;
}

export interface FaultReview {
  fault_likely: number;
  reason: string;
}

const NIGHT_PLAN_SYSTEM =
  "You plan one night of a robotic spectroscopic survey. The input lists tonight's weather forecast notices and the " +
  "current bulletin; each notice is an event kind and a compass sector (N, NE, E, SE, S, SW, W, NW) or ALL (the " +
  "whole sky). Decide two things.\n" +
  "bad_night: true when tonight's forecast or bulletin has rain, storm, overcast or haze over ALL of the sky. Faint " +
  "must-observe targets need a one-hour exposure in a clear sky, so on a bad night they should wait for a better " +
  "night.\n" +
  "avoid_directions: the sectors with rain, storm, overcast, haze or cold_snap tonight. Ignore earthquake, " +
  "rocket_launch and terrain_obstruction (the scheduler handles those itself). Never list a sector nothing names.\n" +
  'Reply with one JSON object only: {"bad_night": true|false, "avoid_directions": ["SW", ...], "reason": "<12 words"}';

const FAULT_REVIEW_SYSTEM =
  "You watch the data quality of a robotic telescope. An instrument fault is never announced: it lowers the " +
  "instrument efficiency, and so the quality of every exposure, until someone reports it; a correct report " +
  "repairs it at once. An earthquake (it appears in the bulletin) also lowers instrument efficiency, and that loss " +
  "fades night by night; a report does not repair it. Weather lowers quality too, but it also lowers the program " +
  "band, which the instrument does not affect.\n" +
  "Columns per hour: E = measured quality / quality the program bands allow (about 1 when healthy; low when the " +
  "instrument is the cause; in a very clear sky the bands bound it only loosely, so it can stay near 1), scale = " +
  "measured sky quality relative to the clear-sky model, ref = the usual scale since the last repair. " +
  "notices_now lists the current bulletin.\n" +
  "Signs of a fault: quality that drops and stays down without recovering, E low for many hours across nights, " +
  "not explained by announced weather or by a recent earthquake whose effect is fading.\n" +
  "Reporting: a correct report earns 100 and repairs the instrument; false reports are free while " +
  "free_false_reports_left > 0, afterwards each costs 150.\n" +
  'Reply with one JSON object only: {"fault_likely": <0..1>, "reason": "<15 words"}';

const CONFIRM_SYSTEM =
  "You check the evidence for an unannounced instrument fault on a robotic telescope before a paid report. A false " +
  "report costs 150 points; a correct one earns 100 and repairs the instrument. E per hour = measured quality / " +
  "quality the program bands allow: about 1 when healthy, low while the instrument is the cause. Weather lowers both " +
  "quality and band; an earthquake lowers instrument efficiency in a way that fades night by night and that a " +
  "report does not repair.\n" +
  'Reply with one JSON object only: {"report": true|false, "reason": "<15 words"}';

const isObject = (value: unknown): value is Json => value !== null && typeof value === "object" && !Array.isArray(value);

/** Python's float(): numbers, numeric strings and booleans; anything else is not a number. */
function pyFloat(value: unknown): number | null {
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value.trim());
    return Number.isNaN(parsed) ? null : parsed;
  }
  return null;
}

export class Advisor {
  private planCall: Call | null = null;
  private faultCall: Call | null = null;
  private planApplied = true;
  private faultApplied = true;
  nightDate: string | null = null;
  private announced = new Set<string | undefined>();

  constructor(private readonly client: LLMClient, private readonly log: (text: string) => void = () => {}) {}

  // --- night start ----------------------------------------------------------------------------------

  /** Submit both calls; wait up to waitSeconds for them. Returns [plan, fault] answers that are ready. */
  async startNight(
    nightDate: string,
    tonight: Notice[],
    bulletin: Notice[],
    faultTable: Json,
    wallclockLeft: number,
    waitSeconds: number,
  ): Promise<[NightPlan | null, FaultReview | null]> {
    this.nightDate = nightDate;
    this.announced = new Set([...tonight, ...bulletin].filter((n) => WEATHER_KINDS.has(n.event_kind as string)).map((n) => n.direction));
    const notices = {
      night: nightDate,
      forecast_tonight: tonight.map((n) => ({ event_kind: n.event_kind ?? null, direction: n.direction ?? null })),
      bulletin_now: bulletin.map((n) => ({ event_kind: n.event_kind ?? null, direction: n.direction ?? null })),
    };
    this.planCall = this.client.submit("night_plan", NIGHT_PLAN_SYSTEM, notices, wallclockLeft);
    this.faultCall = this.client.submit("fault_review", FAULT_REVIEW_SYSTEM, faultTable, wallclockLeft);
    this.planApplied = this.planCall === null;
    this.faultApplied = this.faultCall === null;
    const deadline = performance.now() + Math.max(0.0, waitSeconds) * 1000;
    for (const call of [this.planCall, this.faultCall]) {
      if (call !== null) await call.wait((deadline - performance.now()) / 1000);
    }
    return this.poll();
  }

  /** [plan, fault] answers that arrived since the last poll; null for each one not (newly) available. */
  poll(): [NightPlan | null, FaultReview | null] {
    let plan: NightPlan | null = null;
    let fault: FaultReview | null = null;
    if (!this.planApplied && this.planCall !== null && this.planCall.done()) {
      this.planApplied = true;
      plan = this.validPlan(this.client.collect(this.planCall));
    }
    if (!this.faultApplied && this.faultCall !== null && this.faultCall.done()) {
      this.faultApplied = true;
      fault = Advisor.validFault(this.client.collect(this.faultCall));
    }
    return [plan, fault];
  }

  private validPlan(answer: Json | null): NightPlan | null {
    if (!isObject(answer) || typeof answer.bad_night !== "boolean") return null;
    const avoid = answer.avoid_directions ?? [];
    if (!Array.isArray(avoid)) return null;
    // the model may rank announced weather; it may not close sky that nothing announced
    const kept = new Set<string>();
    for (const d of avoid) {
      const name = String(d).toUpperCase();
      if (DIRECTIONS.has(name) && this.announced.has(name)) kept.add(name);
    }
    return { bad_night: answer.bad_night, avoid_directions: [...kept].sort(), reason: String(answer.reason ?? "").slice(0, 80) };
  }

  private static validFault(answer: Json | null): FaultReview | null {
    if (!isObject(answer)) return null;
    const p = pyFloat(answer.fault_likely);
    if (p === null || !(p >= 0.0 && p <= 1.0)) return null;
    return { fault_likely: p, reason: String(answer.reason ?? "").slice(0, 80) };
  }

  // --- paid report confirmation ------------------------------------------------------------------------

  /** true / false from the model, or null (no answer in time: the rule decides). */
  async confirmReport(evidence: Json, wallclockLeft: number, waitSeconds: number): Promise<boolean | null> {
    const call = this.client.submit("confirm_report", CONFIRM_SYSTEM, evidence, wallclockLeft);
    if (call === null) return null;
    await call.wait(waitSeconds);
    const answer = this.client.collect(call);
    if (isObject(answer) && typeof answer.report === "boolean") return answer.report;
    return null;
  }
}
