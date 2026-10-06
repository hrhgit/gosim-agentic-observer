/** Comparison companion. Fixed search breadth applies only to this offline tool. */
import { Agent, cpu_seconds, raw_lines } from "../agent";
import { Payload } from "../core/compat";
const digest = (agent: Agent) => ({
  progress:
    [...agent.sv.ledger.values()]
      .at(-1)
      ?.map(([j]) => [j, agent.sv.best[j], agent.sv.gmax[j]]) ?? [],
  required_left: agent.sv.required_left,
  band_done: [...agent.sv.band_done],
  extra: [...agent.sv.extra],
  zextra: [...agent.sv.zextra],
  sky: {
    W: agent.sv.sky.W,
    e: agent.sv.sky.e,
    rho: agent.sv.sky.rho,
    suspect: agent.sv.sky.suspect,
    closed_suspect: agent.sv.sky.closed_suspect,
    low_count: agent.sv.sky.low_count,
    low_seconds: agent.sv.sky.low_seconds,
    free_left: agent.sv.sky.free_left,
    reports_correct: agent.sv.sky.reports_correct,
    reports_false: agent.sv.sky.reports_false,
  },
  offset: {
    alt: agent.sv.offset.alt,
    az: agent.sv.offset.az,
    margin_alt: agent.sv.offset.margin_alt,
    margin_az: agent.sv.offset.margin_az,
    active: agent.sv.offset.active,
    solves: agent.sv.offset.solves,
    best_mismatch: agent.sv.offset.best_mismatch,
  },
  pending: agent.pending,
});
async function main() {
  let agent: Agent | null = null;
  const plans: unknown[] = [];
  for await (const line of raw_lines(process.stdin)) {
    const message: Payload = JSON.parse(line),
      before = cpu_seconds();
    let action: Payload | null = null;
    if (message.message_type === "initialize") {
      agent = new Agent(message.payload);
      agent._pace = function (_payload, t, _night) {
        this.now = t;
        this.k = 14;
        this.t_floor = this.sv.t_min;
      };
      const original = agent.planner._evaluate.bind(agent.planner);
      agent.planner._evaluate = (...args) => {
        const plan = original(...args);
        plans.push(plan);
        return plan;
      };
    } else if (message.message_type === "decision_request") {
      if (agent === null) throw new Error("missing initialize");
      plans.length = 0;
      action = agent.decide(message.payload);
    } else if (message.message_type === "inspect_plans") {
      process.stdout.write(JSON.stringify({ plans }) + "\n");
      continue;
    } else if (message.message_type === "sync_pending") {
      if (agent === null) throw new Error("missing initialize");
      agent.pending = message.pending;
      process.stdout.write("{}\n");
      continue;
    } else if (message.message_type === "finish") break;
    const response = {
      action,
      state: agent === null ? null : digest(agent),
      cpu_seconds: cpu_seconds() - before,
    };
    await new Promise<void>((resolve, reject) =>
      process.stdout.write(JSON.stringify(response) + "\n", (e) =>
        e ? reject(e) : resolve(),
      ),
    );
  }
}
main().catch((error) => {
  process.stderr.write(String(error.stack ?? error) + "\n");
  process.exitCode = 1;
});
