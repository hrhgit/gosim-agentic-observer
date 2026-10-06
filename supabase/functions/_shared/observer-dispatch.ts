import { decryptCredential } from "./observer-model.ts";
import type { Rpc } from "./observer-model.ts";
import { GitHubApp, GitHubError } from "./observer-github.ts";

type LoadRow = {
  organization: string;
  approved_sha: string;
  over_limit: boolean;
};

type PendingJob = {
  id: string;
  kind: string;
  organization: string;
  workflow_sha: string;
  encrypted_nonce: string;
  runner?: "github-hosted" | "self-hosted" | "public-hosted";
  // Set by observer_pending_jobs while a self-hosted fallback slot is free.
  fallback?: "stalled" | "over_limit" | null;
  // Set by observer_pending_jobs when the public runner pool should take the job.
  public?: boolean | null;
};

type Outcome = {
  id: string;
  dispatched: boolean;
  error?: string;
  failover?: string;
  fallback?: string;
  public?: string;
};

type Dispatcher = Pick<GitHubApp, "dispatch"> & Partial<Pick<GitHubApp, "dispatchPublic">>;

// Organization-level failures (missing/suspended installation, rejected or
// quota-exhausted API calls, unapproved control repository) cannot fix
// themselves within the job lease: the job moves to the next best
// organization instead of retrying the broken one until expiry.
const ORGANIZATION_FAILURES = new Set([
  "organization_not_installed",
  "installation_identity_mismatch",
  "control_repository_must_be_private",
  "control_revision_not_approved",
  "github_not_found",
  "github_request_failed",
]);

async function tryDispatch(
  rpc: Rpc,
  app: Dispatcher,
  job: { id: string; kind: string },
  organization: string,
  nonce: string,
  approvedSha: string,
  selfHosted = false,
): Promise<{ ok: boolean; error?: string }> {
  try {
    const workflow = "observer-" + job.kind + ".yml" as Parameters<GitHubApp["dispatch"]>[1];
    if (selfHosted) await app.dispatch(organization, workflow, job.id, nonce, approvedSha, "self-hosted");
    else await app.dispatch(organization, workflow, job.id, nonce, approvedSha);
    await rpc("observer_mark_dispatched", { p_job: job.id });
    return { ok: true };
  } catch (error) {
    const code = error instanceof GitHubError ? error.code : "dispatch_unavailable";
    await rpc("observer_dispatch_error", { p_job: job.id, p_error: code });
    return { ok: false, error: code };
  }
}

// The public-repository pool (ops/public-runner-pool.md): an overflow for
// colocated engine jobs while private organizations are near their monthly
// minutes or busy. The only dispatch input is the job id; the run id GitHub
// returns binds the claim. Any failure sends the job straight back to its own
// organization, so the public pool can never strand a job. Returns null when
// the pool does not take the job (switched off, phase not enabled, at capacity).
async function tryPublic(rpc: Rpc, app: Dispatcher, job: PendingJob, moved = false): Promise<Outcome | null> {
  let target: { organization: string; approved_sha: string; repository_id: string } | null = null;
  try {
    target = await rpc(moved ? "observer_public_target" : "observer_public_job", { p_job: job.id });
  } catch {
    target = null;
  }
  if (!target?.organization) {
    if (!moved) return null;
    await rpc("observer_public_return_job", { p_job: job.id, p_error: "public_pool_unavailable" });
    return { id: job.id, dispatched: false, error: "public_pool_unavailable" };
  }
  try {
    if (!app.dispatchPublic) throw new GitHubError("public_pool_unavailable");
    const run = await app.dispatchPublic(
      target.organization,
      target.repository_id,
      job.id,
      target.approved_sha,
      job.kind === "score" ? "score" : "engine",
    );
    await rpc("observer_mark_public_dispatched", { p_job: job.id, p_github_run: run });
    return { id: job.id, dispatched: true, public: target.organization };
  } catch (error) {
    const code = error instanceof GitHubError ? error.code : "dispatch_unavailable";
    await rpc("observer_public_return_job", { p_job: job.id, p_error: code });
    return { id: job.id, dispatched: false, error: code };
  }
}

// Last resort: the organizer's self-hosted fallback runner. Returns null when no
// fallback slot is free, so the caller keeps its GitHub-hosted outcome. A split
// run's partner job moves along; it is recorded in partners and dispatched on the
// next round from its new row.
async function tryFallback(
  rpc: Rpc,
  app: Dispatcher,
  job: PendingJob,
  nonce: string,
  partners: Set<string>,
  failed: string[] = [],
): Promise<Outcome | null> {
  let target: { organization: string; approved_sha: string; partner?: string | null };
  try {
    target = await rpc("observer_fallback_job", { p_job: job.id, p_avoid: failed });
  } catch {
    return null;
  }
  if (!target?.organization) return null;
  if (target.partner) partners.add(target.partner);
  const result = await tryDispatch(rpc, app, job, target.organization, nonce, target.approved_sha, true);
  return result.ok
    ? { id: job.id, dispatched: true, fallback: target.organization }
    : { id: job.id, dispatched: false, error: result.error };
}

export async function dispatchPending(rpc: Rpc, app: Dispatcher, masterKey: string, limit = 10) {
  await rpc("observer_reconcile_jobs", {});
  await rpc("observer_reconcile_sessions", {});
  // Public-pool runs that never started go back to their own organization.
  // Never let the optional pool hold up dispatching.
  await rpc("observer_public_pool_reconcile", {}).catch(() => 0);
  const jobs: PendingJob[] = await rpc("observer_pending_jobs", { p_limit: limit });
  const partners = new Set<string>();
  const one = async (job: PendingJob): Promise<Outcome> => {
    if (partners.has(job.id)) {
      return { id: job.id, dispatched: false, error: "moved_to_fallback" };
    }
    try {
      if (job.runner === "public-hosted") {
        // Already in the pool (an ambiguous earlier dispatch): dispatched there
        // again, or sent home and dispatched there next round.
        return await tryPublic(rpc, app, job, true) ?? { id: job.id, dispatched: false };
      }
      if (job.public) {
        const outcome = await tryPublic(rpc, app, job);
        if (outcome?.dispatched) {
          return outcome;
        }
        // Declined, or failed and sent back for good: this round's attempt goes
        // to the job's own organization as usual.
      }
      const nonce = await decryptCredential(job.encrypted_nonce, job.id + ":nonce", masterKey);
      if (job.fallback) {
        const moved = await tryFallback(rpc, app, job, nonce, partners);
        if (moved) {
          return moved;
        }
        // A stalled job was already dispatched three times to its organization.
        if (job.fallback === "stalled") {
          return { id: job.id, dispatched: false, error: "github_run_not_started" };
        }
      }
      const selfHosted = job.runner === "self-hosted";
      const first = await tryDispatch(rpc, app, job, job.organization, nonce, job.workflow_sha, selfHosted);
      if (first.ok) {
        return { id: job.id, dispatched: true };
      }
      if (selfHosted || !ORGANIZATION_FAILURES.has(first.error ?? "")) {
        return { id: job.id, dispatched: false, error: first.error };
      }
      const candidates: LoadRow[] = await rpc("observer_organizations_by_load", {});
      const next = candidates.find((c) => c.organization !== job.organization && !c.over_limit);
      let error = first.error;
      const failed = [job.organization];
      if (next) {
        // Moves the job and the owner's recorded placement atomically.
        await rpc("observer_failover_job", { p_job: job.id, p_organization: next.organization });
        const retried = await tryDispatch(rpc, app, job, next.organization, nonce, next.approved_sha);
        if (retried.ok) {
          return { id: job.id, dispatched: true, failover: next.organization };
        }
        error = retried.error;
        failed.push(next.organization);
      }
      // No private organization can take the job right now: the public pool
      // when it takes this job, else the self-hosted fallback.
      const publicOutcome = ORGANIZATION_FAILURES.has(error ?? "") ? await tryPublic(rpc, app, job) : null;
      const moved = publicOutcome?.dispatched
        ? publicOutcome
        : ORGANIZATION_FAILURES.has(error ?? "")
        ? await tryFallback(rpc, app, job, nonce, partners, failed)
        : null;
      return moved ?? { id: job.id, dispatched: false, error };
    } catch (error) {
      const code = error instanceof GitHubError ? error.code : "dispatch_unavailable";
      await rpc("observer_dispatch_error", { p_job: job.id, p_error: code });
      return { id: job.id, dispatched: false, error: code };
    }
  };
  // Jobs are independent rows reserved by observer_pending_jobs, so they are
  // dispatched concurrently. A round that may move a split run's partner to the
  // self-hosted fallback stays sequential (the partner check needs the order).
  if (jobs.some((j) => j.fallback)) {
    const outcomes: Outcome[] = [];
    for (const job of jobs) outcomes.push(await one(job));
    return outcomes;
  }
  return await Promise.all(jobs.map(one));
}
