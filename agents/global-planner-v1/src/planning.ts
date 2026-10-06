import type { Agent } from "./agent";
import { Plan } from "./core/planner";
import { Payload } from "./core/compat";
import { format_utc } from "./core/astro";

/** Only public agent state. Candidate labels/IDs stay outside numeric model features. */
export async function planningFrame(agent: Agent, plans: Plan[], actions: Payload[], teacher: number,
  payload: Payload, t: number, night: number): Promise<Payload> {
  const sv = agent.sv, cat = sv.cat, sky = sv.sky;
  let conditions: any[] = [];
  if (agent.modelSession.enabled.includes("conditions-reliability")) {
    const queries = plans.flatMap((p, i) => [900, 1800, 3600].map(lead => ({
      candidate_id: `${i}:${lead}`, ra_deg: cat.zra[p.zone], dec_deg: cat.zdec[p.zone],
      start_utc: format_utc(t + lead), duration_seconds: actions[i].duration_seconds,
    })));
    const result = await agent.modelSession.conditions(format_utc(t), queries);
    conditions = result.predictions;
  }
  const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);
  const weight = Math.max(sum(cat.weight), 1);
  const remaining = Math.max(0, sv.nights[night][1] - t) + agent.suffix[night + 1];
  return {
    schema: "global-planning-frame-v1", teacher,
    global: {
      night_fraction_left: (sv.nights.length - night) / sv.nights.length,
      total_time_fraction_left: remaining / Math.max(1, sv.total_night_seconds),
      tonight_hours_left: Math.max(0, sv.nights[night][1] - t) / 3600,
      science_fraction: Number(payload.running_total || 0) / weight,
      required_fraction_left: sv.required_left / Math.max(1, cat.required.filter(Boolean).length),
      active_requests: payload.active_requests ?? [],
      baseline: agent.modelState(),
      faults: agent.modelSession.latestFault,
      last_result: payload.last_result,
      bulletin: payload.latest_bulletin, forecast: payload.latest_forecast,
      new_messages: payload.new_messages ?? [],
      bands: [...cat.band_total].map(([b, n]) => ({band: b, fraction: (sv.band_done.get(b) || 0) / Math.max(1, n)})),
      regions: cat.zmembers.map((members, z) => ({
        ra_deg: cat.zra[z], dec_deg: cat.zdec[z], potential: sv.zpot[z] / weight,
        deep: sv.zdeep[z] / weight, required_left: sv.zreq[z].length / Math.max(1, members.length),
        future: cat.season_future[cat.zcoarse[z]][night],
      })),
    },
    now_utc: payload.now_utc,
    candidates: plans.map((p, i) => ({
      id: `candidate-${i}`, action: actions[i],
      features: {
        duration_hours: actions[i].duration_seconds / 3600,
        nv: p.nv / weight, quality: p.q,
        urgency: p.u_s, expected_science: (p.curve.find(r => r[0] === p.duration)?.[3] ?? 0) / weight,
        ra_deg: cat.zra[p.zone], dec_deg: cat.zdec[p.zone],
        future: cat.season_future[cat.zcoarse[p.zone]][night],
        potential: sv.zpot[p.zone] / weight, deep: sv.zdeep[p.zone] / weight,
        required_left: sv.zreq[p.zone].length / Math.max(1, cat.zmembers[p.zone].length),
        assigned_fraction: Object.keys(actions[i].assignments).length / sv.n_fibers,
        targets: p.picks.map(([, j]) => ({required: cat.required[j], science_weight: cat.weight[j],
          best: sv.best[j], factor_estimate: sv.gmax[j], request: sv.extra.get(j) ?? null})),
        conditions: conditions.filter(r => String(r.candidate_id).startsWith(`${i}:`)),
      },
    })),
  };
}
