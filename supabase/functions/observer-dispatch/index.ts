import { createClient } from "npm:@supabase/supabase-js@2";
import { dispatchPending } from "../_shared/observer-dispatch.ts";
import { databaseLocator, GitHubApp } from "../_shared/observer-github.ts";
import { ProxyError } from "../_shared/observer-model.ts";
import { scheduleRuns, scheduleScores } from "../_shared/observer-orchestrate.ts";
import { schedulePreparations } from "../_shared/observer-prepare.ts";
import { cleanupSealed, cleanupUploads } from "../_shared/observer-cleanup.ts";

const service = createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "", {
  auth: { persistSession: false },
});
type Limits = {
  preparations: number;
  runs: number;
  scores: number;
  jobs: number;
  max_passes: number;
  pass_seconds: number;
};
// The per-pass limits before observer_dispatch_limits existed (the rollback values).
const ONE_PASS: Limits = { preparations: 3, runs: 5, scores: 5, jobs: 10, max_passes: 1, pass_seconds: 40 };
async function dispatchLimits(rpc: (name: string, args: Record<string, unknown>) => Promise<unknown>): Promise<Limits> {
  try {
    const v = (await rpc("observer_dispatch_limits", {})) as Record<string, unknown> | null;
    const n = (x: unknown, lo: number, hi: number, fallback: number) =>
      Number.isInteger(x) && (x as number) >= lo && (x as number) <= hi ? x as number : fallback;
    return {
      preparations: n(v?.preparations, 1, 10, 3),
      runs: n(v?.runs, 1, 10, 5),
      scores: n(v?.scores, 1, 20, 5),
      jobs: n(v?.jobs, 1, 20, 10),
      max_passes: n(v?.max_passes, 1, 20, 1),
      pass_seconds: n(v?.pass_seconds, 5, 45, 40),
    };
  } catch {
    return ONE_PASS;
  }
}
const rpc = async (name: string, args: Record<string, unknown>) => {
  const { data, error } = await service.rpc(name, args);
  if (error) throw new ProxyError(503, "dispatch_database_unavailable");
  return data;
};

Deno.serve({ port: Number(Deno.env.get("OBSERVER_LISTEN_PORT") ?? 8000) }, async (request) => {
  try {
    const expected = Deno.env.get("OBSERVER_DISPATCH_SECRET");
    if (!expected || expected.length < 40 || request.headers.get("authorization") !== "Bearer " + expected) {
      throw new ProxyError(401, "dispatcher_only");
    }
    if (request.method !== "POST") throw new ProxyError(405, "method_not_allowed");
    const configs = await rpc("observer_runner_configuration", {});
    const installations = Object.fromEntries(
      configs.map((c: { organization: string; installation_id: number }) => [c.organization, c.installation_id]),
    );
    const app = new GitHubApp(
      Deno.env.get("OBSERVER_GITHUB_APP_ID") ?? "",
      Deno.env.get("OBSERVER_GITHUB_APP_PEM") ?? "",
      installations,
      databaseLocator(rpc),
    );
    const masterKey = Deno.env.get("OBSERVER_KEY_ENCRYPTION_KEY") ?? "";
    await rpc("observer_reconcile_jobs", {});
    await rpc("observer_reconcile_sessions", {});
    // Throughput (organizer-tunable, observer_dispatch_limits): one invocation repeats
    // the schedule-and-dispatch pass until a pass finds no work, max_passes is reached
    // or pass_seconds have elapsed. Rows are leased / locked with skip locked and a
    // dispatched job is not re-dispatched for two minutes, so repeated passes never
    // dispatch a job twice. Without the limits row: one pass with the old per-pass limits.
    const limits = await dispatchLimits(rpc);
    const apiBase = Deno.env.get("SUPABASE_URL") ?? "";
    const started = Date.now();
    const prepared: unknown[] = [], scheduled: unknown[] = [], rescoring: unknown[] = [], dispatched: unknown[] = [];
    let passes = 0;
    while (passes < limits.max_passes) {
      passes++;
      // Each stage is isolated: a failure while scheduling new preparations or runs
      // (a GitHub outage while creating a repository, a bad row) never holds up
      // dispatching the jobs that are already queued, nor the other stages.
      await rpc("observer_reconcile_preparations", {}).catch(() => 0);
      const p = await schedulePreparations({ rpc, app, masterKey, apiBase, limit: limits.preparations })
        .catch(() => [{ error: "preparation_scheduling_unavailable" }]);
      const s = await scheduleRuns({
        rpc,
        masterKey,
        apiBase,
        limit: limits.runs,
        ensureRepository: (user) => app.privateParticipantRepository(user),
      }).catch(() => [{ error: "run_scheduling_unavailable" }]);
      // The rescore never holds up dispatching evaluations.
      const r = await scheduleScores({ rpc, masterKey, limit: limits.scores })
        .catch(() => [{ error: "rescore_unavailable" }]);
      const d = await dispatchPending(rpc, app, masterKey, limits.jobs);
      prepared.push(...p);
      scheduled.push(...s);
      rescoring.push(...r);
      dispatched.push(...d);
      const worked = (list: unknown[]) =>
        list.some((x) =>
          (x as { scheduled?: boolean; dispatched?: boolean })?.scheduled || (x as { dispatched?: boolean })?.dispatched
        );
      if (!worked(p) && !worked(s) && !worked(r) && !worked(d)) break;
      if (Date.now() - started > limits.pass_seconds * 1000) break;
    }
    const cleaned = await cleanupUploads(rpc, async (path) => {
      const { error } = await service.storage.from("observer-staging").remove([path]);
      if (error) throw new ProxyError(503, "temporary_cleanup_failed");
    }).catch(() => 0);
    const sealed = await cleanupSealed(rpc, async (paths) => {
      const { error } = await service.storage.from("observer-staging").remove(paths);
      if (error) throw new ProxyError(503, "temporary_cleanup_failed");
    }).catch(() => 0);
    const data = { passes, prepared, scheduled, rescoring, dispatched, cleaned, sealed };
    return Response.json({ data }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return Response.json({ error: error instanceof ProxyError ? error.code : "dispatch_unavailable" }, {
      status: error instanceof ProxyError ? error.status : 503,
    });
  }
});
