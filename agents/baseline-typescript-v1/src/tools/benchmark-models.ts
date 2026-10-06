/** Full selected-model calls, including local transport and response parsing. */
import { readFileSync } from "node:fs";
import * as path from "node:path";
import { performance } from "node:perf_hooks";
import { BaselineModels, ReliabilityRequest } from "../models";
export async function benchmarkModels(): Promise<unknown> {
  const models = new BaselineModels();
  const example: ReliabilityRequest = JSON.parse(readFileSync(path.resolve(__dirname, "../../models/example-reliability-request.json"), "utf8"));
  try {
    const first = performance.now(), result = await models.predictReliability(example);
    const coldMs = performance.now() - first;
    const index = result.predictions.findIndex(r => r.eligible);
    if (index < 0) throw new Error("Example has no eligible position");
    const seed = example.candidates[index], rows = [];
    for (const count of [16, 100, 1024]) {
      const request: ReliabilityRequest = { ...example, candidates: Array.from({ length: count }, (_, i) => ({
        ...seed, candidate_id: String(i), ra_deg: (seed.ra_deg + i * .0001) % 360,
      })) };
      for (let i = 0; i < 5; i++) await models.predictReliability(request);
      const times: number[] = [], cpu: number[] = [];
      let eligible = 0;
      for (let i = 0; i < 30; i++) {
        const began = performance.now(), beganCpu = models.worker.cpuSeconds;
        const response = await models.predictReliability(request);
        times.push(performance.now() - began); cpu.push((models.worker.cpuSeconds - beganCpu) * 1000);
        eligible = response.predictions.filter(p => p.eligible).length;
      }
      times.sort((a, b) => a - b); cpu.sort((a, b) => a - b);
      const q = (values: number[], probability: number): number => {
        const at = (values.length - 1) * probability, lo = Math.floor(at);
        return values[lo] + (values[Math.ceil(at)] - values[lo]) * (at - lo);
      };
      rows.push({ candidates: count, unique_positions: count, eligible,
        full_call_p50_ms: q(times, .5), full_call_p95_ms: q(times, .95), worker_cpu_p95_ms: q(cpu, .95) });
    }
    return { model_id: result.model_id, model_version: result.model_version, node: process.version,
      platform: process.platform, architecture: process.arch, cold_first_call_ms: coldMs, warmup: 5, repetitions: 30, rows,
      boundary: "One future exposure: public features, frozen foundation/correction/head/calibration, JSON transport and output parsing; excludes agent planning and stdout logging.",
      source: "Formal public A initialize plus explicitly SELF-GENERATED positions; local timing, not competition server timing." };
  } finally { await models.close(); }
}
if (require.main === module) benchmarkModels().then(result => process.stdout.write(JSON.stringify(result, null, 2) + "\n"))
  .catch(error => { process.stderr.write(String(error) + "\n"); process.exitCode = 1; });
