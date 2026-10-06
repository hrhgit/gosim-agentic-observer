import { decryptCredential, encryptCredential, ProxyError } from "./observer-model.ts";
import type { Rpc } from "./observer-model.ts";
import { databaseLocator, GitHubError, placement } from "./observer-github.ts";

export interface RunScheduler {
  rpc: Rpc;
  masterKey: string;
  apiBase: string;
  ensureRepository: (user: string) => Promise<unknown>;
  /** Runs per pass (observer_dispatch_limits.runs; the database caps it at 10). */
  limit?: number;
}

export function randomCapability() {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
    .replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

/**
 * A run's team variables (secret values decrypted here, in memory only) and allowed
 * domains, for the job input. The job input itself is encrypted before it is stored,
 * and the runner receives it only through its authenticated claim.
 */
export async function teamEgress(deps: Pick<RunScheduler, "rpc" | "masterKey">, run: string) {
  const value = await deps.rpc("observer_run_team_egress", { p_run: run });
  return {
    team: await decodeTeamEgress(value, deps.masterKey),
    // An evaluation without a model (本次不提供模型): the database has already left out the
    // team's model variables; the job adds OBSERVER_MODEL_DISABLED=1 to the project's environment.
    modelDisabled: value?.model_disabled === true,
  };
}

/** The job-input form of observer_run_team_egress / observer_preparation_team_egress (null while off). */
export async function decodeTeamEgress(
  value: {
    enabled?: boolean;
    open?: boolean;
    variables?: Record<string, unknown>[];
    domains?: string[];
    route?: { name?: unknown; fallback?: unknown; cap_bytes?: unknown } | null;
  } | null,
  masterKey: string,
) {
  if (value?.enabled !== true) return null;
  const environment: Record<string, string> = {}, secrets: string[] = [];
  for (const variable of value?.variables ?? []) {
    environment[String(variable.name)] = variable.secret
      ? await decryptCredential(String(variable.encrypted_value ?? ""), String(variable.id), masterKey)
      : String(variable.plain_value ?? "");
    if (variable.secret) secrets.push(String(variable.name));
  }
  // Open egress: any public destination (the domain list is not used then).
  // The egress route (labels only; the job API adds the node settings at claim time).
  if (value?.open === true) {
    const route = value.route;
    return {
      environment,
      secrets,
      domains: [] as string[],
      open: true as const,
      ...(route && (route.name === "cn" || route.name === "overseas") && typeof route.fallback === "boolean" &&
          Number.isSafeInteger(route.cap_bytes) && Number(route.cap_bytes) > 0
        ? { route: { name: route.name, fallback: route.fallback, cap_bytes: Number(route.cap_bytes) } }
        : {}),
    };
  }
  return { environment, secrets, domains: [...(value?.domains ?? [])] as string[] };
}

export async function scheduleRuns(deps: RunScheduler) {
  const base = new URL(deps.apiBase);
  if (base.protocol !== "https:" || base.username || base.password || base.search || base.hash || base.port) {
    throw new ProxyError(503, "invalid_platform_url");
  }
  const api = base.href.replace(/\/$/, "") + "/functions/v1/";
  // A partially configured deployment does not consume participant retry limits.
  const installations = await deps.rpc("observer_runner_configuration", {});
  if (!installations.length) return [];
  const enabled = new Set(installations.map((i: { organization: string }) => i.organization));
  const runs = await deps.rpc("observer_pending_runs", { p_limit: deps.limit ?? 5 });
  // Organizer switch, read once per pass and only when a colocated run needs it:
  // model-proxy-only egress for colocated containers. Team egress is decided per
  // run by observer_run_team_egress (global switch or pilot team).
  let switches: Promise<{ restricted_egress?: boolean } | null> | undefined;
  const hardening = () => switches ??= deps.rpc("observer_hardening", {});
  const egressSwitch = async () => (await hardening())?.restricted_egress === true;
  // Runs are independent rows (each with its own lease), so they are handled
  // concurrently; the database caps a pass at 10 runs / 20 score jobs.
  // deno-lint-ignore no-explicit-any
  return await Promise.all(runs.map(async (run: any) => {
    try {
      const { organization } = await placement(run.user_id, databaseLocator(deps.rpc));
      if (!enabled.has(organization)) throw new GitHubError("runner_not_configured");
      // Local sessions need a private result repository too. Provision it before
      // opening a capability; a GitHub failure cannot leave a half-started run.
      await deps.ensureRepository(run.user_id);
      const instance = await deps.rpc("observer_instance_input", { p_run: run.id });
      // Public scenarios run the participant container inside the engine job:
      // the same step-by-step protocol, without a database round trip per step.
      const colocated = run.mode === "project" && !instance &&
        await deps.rpc("observer_run_colocated", { p_run: run.id }) === true;
      const restricted = colocated && await egressSwitch();
      // Team egress for this run's team: globally, or as a listed pilot team.
      const { team, modelDisabled } = run.mode === "project"
        ? await teamEgress(deps, run.id)
        : { team: null, modelDisabled: false };
      const noModel = modelDisabled ? { model_disabled: true } : {};
      const participant = randomCapability(), engine = randomCapability();
      const jobs = [];
      const encodeJob = async (kind: string, input: Record<string, unknown>) => {
        const id = crypto.randomUUID(), nonce = randomCapability();
        return {
          id,
          kind,
          nonce,
          encrypted_nonce: await encryptCredential(nonce, id + ":nonce", deps.masterKey),
          encrypted_input: await encryptCredential(JSON.stringify({ ...input, kind, job_id: id }), id, deps.masterKey),
        };
      };
      jobs.push(
        await encodeJob("engine", {
          run_id: run.id,
          run_credential: "obs_" + run.id + "." + engine,
          session_url: api + "observer-session",
          scenario_ref: { bucket: "observer-scenarios", path: run.storage_path },
          scenario_digest: run.scenario_digest,
          runtime_seconds: run.runtime_seconds,
          artifact_upload: { kind: "github" },
          ...(instance ? { instance } : {}),
          ...(colocated
            ? {
              ...(restricted ? { restricted_egress: true } : {}),
              ...(team ? { team_egress: team } : {}),
              ...noModel,
              archive_ref: run.archive_ref,
              colocated: {
                run_credential: "obs_" + run.id + "." + participant,
                model_base_url: api + "observer-model/v1",
                source_digest: run.materialized_digest,
                manifest: run.manifest,
              },
            }
            : {}),
        }),
      );
      if (run.mode === "project" && !colocated) {
        jobs.push(
          await encodeJob("execute", {
            run_id: run.id,
            run_credential: "obs_" + run.id + "." + participant,
            session_url: api + "observer-session",
            model_base_url: api + "observer-model/v1",
            ...(team ? { team_egress: team } : {}),
            ...noModel,
            archive_ref: run.archive_ref,
            source_digest: run.materialized_digest,
            manifest: run.manifest,
          }),
        );
      }
      const local = run.mode === "local"
        ? await encryptCredential("obs_" + run.id + "." + participant, run.id + ":local", deps.masterKey)
        : null;
      // Session, encrypted local handoff and all jobs commit together. A lost
      // response is retried with the same lease and cannot start a second run.
      await deps.rpc("observer_schedule_run", {
        p_run: run.id,
        p_lease: run.lease,
        p_organization: organization,
        p_participant_token: participant,
        p_engine_token: engine,
        p_local_credential: local,
        p_jobs: jobs,
      });
      return { id: run.id, scheduled: true };
    } catch (error) {
      const code = error instanceof GitHubError ? error.code : "schedule_unavailable";
      await deps.rpc("observer_run_schedule_error", { p_run: run.id, p_lease: run.lease, p_error: code });
      return { id: run.id, scheduled: false, error: code };
    }
  }));
}

/**
 * The independent rescore: once a formal project run is scored from its engine's
 * summary, a score job on a fresh runner (no participant code) recomputes the
 * score from the committed trace. The database adopts the recomputed score.
 */
export async function scheduleScores(deps: Pick<RunScheduler, "rpc" | "masterKey" | "limit">) {
  const installations = await deps.rpc("observer_runner_configuration", {});
  if (!installations.length) return [];
  const runs = await deps.rpc("observer_pending_score_runs", { p_limit: deps.limit ?? 5 });
  // Runs are independent rows (each with its own lease), so they are handled
  // concurrently; the database caps a pass at 10 runs / 20 score jobs.
  // deno-lint-ignore no-explicit-any
  return await Promise.all(runs.map(async (run: any) => {
    try {
      const id = crypto.randomUUID(), nonce = randomCapability();
      const input = {
        run_id: run.id,
        scenario_ref: { bucket: "observer-scenarios", path: run.storage_path },
        scenario_digest: run.scenario_digest,
        result_ref: run.result_path,
        decisions_digest: run.decisions_digest,
        termination_reason: run.termination_reason,
      };
      await deps.rpc("observer_enqueue_job", {
        p_id: id,
        p_kind: "score",
        p_run: run.id,
        p_revision: null,
        p_organization: run.organization,
        p_nonce: nonce,
        p_encrypted_input: await encryptCredential(
          JSON.stringify({ ...input, kind: "score", job_id: id }),
          id,
          deps.masterKey,
        ),
        p_encrypted_nonce: await encryptCredential(nonce, id + ":nonce", deps.masterKey),
      });
      return { id: run.id, scheduled: true };
    } catch (error) {
      const code = error instanceof GitHubError ? error.code : "schedule_unavailable";
      return { id: run.id, scheduled: false, error: code };
    }
  }));
}
