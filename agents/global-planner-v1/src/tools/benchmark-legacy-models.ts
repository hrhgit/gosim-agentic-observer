import { readFileSync } from "node:fs";
import * as path from "node:path";
import { performance } from "node:perf_hooks";
import { BaselineModels, CONDITION_MODEL_IDS, ConditionsModel, ConditionsRequest, prepareConditions } from "../models";

function measure(run: () => unknown): { p50_ms: number; p95_ms: number } {
  for (let i = 0; i < 5; i++) run();
  const times: number[] = [];
  for (let i = 0; i < 30; i++) { const begin = performance.now(); run(); times.push(performance.now() - begin); }
  times.sort((a, b) => a - b);
  const quantile = (p: number): number => {
    const at = (times.length - 1) * p, left = Math.floor(at);
    return times[left] + (times[Math.ceil(at)] - times[left]) * (at - left);
  };
  return { p50_ms: quantile(.5), p95_ms: quantile(.95) };
}
export function benchmarkModels(): unknown {
  const models = new BaselineModels(), example: ConditionsRequest = JSON.parse(readFileSync(path.resolve(__dirname, "../../models/example-request.json"), "utf8"));
  const first = prepareConditions(example).rows.findIndex(r => r.eligible);
  if (first < 0) throw new Error("example has no eligible position");
  const candidate = example.candidates[first], load: Record<string, number> = {};
  for (const id of CONDITION_MODEL_IDS) {
    const began = performance.now(); models.registry.resolve("conditions", id); load[id] = performance.now() - began;
  }
  const rows = [16, 100, 1024].map(count => {
    // Unique positions sharing an exposure, not repeated rows used to inflate throughput.
    const request: ConditionsRequest = { ...example, candidates: Array.from({ length: count }, (_, i) => ({ ...candidate,
      candidate_id: String(i), ra_deg: (candidate.ra_deg + i * .0001) % 360 })) };
    const prepared = prepareConditions(request);
    const network = Object.fromEntries(CONDITION_MODEL_IDS.map(id => [id,
      measure(() => (models.registry.resolve("conditions", id) as ConditionsModel).numeric(prepared.groups[0]))]));
    return { candidates: count, unique_positions: count,
      eligible: prepared.groups.reduce((s, g) => s + g.indices.length, 0), network,
      full_both_models: measure(() => models.predictConditions(CONDITION_MODEL_IDS, request)) };
  });
  return { node: process.version, platform: process.platform, architecture: process.arch, load_ms: load,
    memory: process.memoryUsage(), warmup: 5, repetitions: 30, rows,
    network_boundary: "One shared history/exposure; normalization, network, numeric output, point calibration; excludes geometry/request processing.",
    full_boundary: "Public feature processing plus both already-loaded models and response construction; excludes process startup and stdout serialization.",
    source: "Formal A public initialize and self-generated messages/positions; local timing, not official server speed." };
}
if (require.main === module) process.stdout.write(JSON.stringify(benchmarkModels(), null, 2) + "\n");
