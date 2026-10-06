import { assert, assertEquals } from "@std/assert";
import { encryptCredential } from "./observer-model.ts";
import { dispatchPending } from "./observer-dispatch.ts";
import { GitHubError } from "./observer-github.ts";

Deno.test("dispatcher decrypts only the nonce and acknowledges the fixed workflow", async () => {
  const id = "00000000-0000-4000-8000-000000000001", key = btoa("k".repeat(32)), nonce = "n".repeat(43);
  const encrypted = await encryptCredential(nonce, id + ":nonce", key);
  const calls: string[] = [];
  const result = await dispatchPending((name) => {
    calls.push(name);
    return Promise.resolve(
      name === "observer_pending_jobs"
        ? [{
          id,
          kind: "engine",
          organization: "AGENTIC-OBSERVER26-runner-1",
          workflow_sha: "a".repeat(40),
          encrypted_nonce: encrypted,
        }]
        : null,
    );
  }, {
    dispatch: (org, workflow, job, token, sha) => {
      assertEquals([org, workflow, job, token, sha], [
        "AGENTIC-OBSERVER26-runner-1",
        "observer-engine.yml",
        id,
        nonce,
        "a".repeat(40),
      ]);
      calls.push("github");
      return Promise.resolve();
    },
  }, key);
  assertEquals(result, [{ id, dispatched: true }]);
  assertEquals(calls, [
    "observer_reconcile_jobs",
    "observer_reconcile_sessions",
    "observer_public_pool_reconcile",
    "observer_pending_jobs",
    "github",
    "observer_mark_dispatched",
  ]);
});

Deno.test("ambiguous dispatch errors retain a recoverable receipt without exposing credentials", async () => {
  const id = "00000000-0000-4000-8000-000000000001", key = btoa("k".repeat(32));
  const encrypted = await encryptCredential("n".repeat(43), id + ":nonce", key);
  const calls: { name: string; args: Record<string, unknown> }[] = [];
  const result = await dispatchPending(
    (name, args) => {
      calls.push({ name, args });
      return Promise.resolve(
        name === "observer_pending_jobs"
          ? [{
            id,
            kind: "execute",
            organization: "AGENTIC-OBSERVER26-runner-1",
            workflow_sha: "a".repeat(40),
            encrypted_nonce: encrypted,
          }]
          : null,
      );
    },
    { dispatch: () => Promise.reject(new GitHubError("github_unavailable")) },
    key,
  );
  assertEquals(result, [{ id, dispatched: false, error: "github_unavailable" }]);
  assertEquals(calls.at(-1), { name: "observer_dispatch_error", args: { p_job: id, p_error: "github_unavailable" } });
});

Deno.test("an organization-level dispatch failure fails the job over to the next best organization", async () => {
  const id = "00000000-0000-4000-8000-000000000001", key = btoa("k".repeat(32)), nonce = "n".repeat(43);
  const encrypted = await encryptCredential(nonce, id + ":nonce", key);
  const calls: { name: string; args: unknown }[] = [];
  const dispatches: string[] = [];
  const result = await dispatchPending(
    (name, args) => {
      calls.push({ name, args });
      if (name === "observer_pending_jobs") {
        return Promise.resolve([{
          id,
          kind: "engine",
          organization: "AGENTIC-OBSERVER26-runner-1",
          workflow_sha: "a".repeat(40),
          encrypted_nonce: encrypted,
        }]);
      }
      if (name === "observer_organizations_by_load") {
        return Promise.resolve([
          { organization: "AGENTIC-OBSERVER26-runner-1", approved_sha: "a".repeat(40), over_limit: false },
          { organization: "AGENTIC-OBSERVER26-runner-2", approved_sha: "b".repeat(40), over_limit: false },
          { organization: "AGENTIC-OBSERVER26-runner-3", approved_sha: "c".repeat(40), over_limit: true },
        ]);
      }
      return Promise.resolve(null);
    },
    {
      dispatch: (org, _workflow, _job, _nonce, sha) => {
        dispatches.push(org + "@" + sha);
        return org === "AGENTIC-OBSERVER26-runner-1"
          ? Promise.reject(new GitHubError("github_request_failed", 403))
          : Promise.resolve();
      },
    },
    key,
  );
  assertEquals(result, [{ id, dispatched: true, failover: "AGENTIC-OBSERVER26-runner-2" }]);
  assertEquals(dispatches, [
    "AGENTIC-OBSERVER26-runner-1@" + "a".repeat(40),
    "AGENTIC-OBSERVER26-runner-2@" + "b".repeat(40),
  ]);
  // runner-1 is excluded as the failed origin and runner-3 as over its minute cap.
  assertEquals(calls.find((c) => c.name === "observer_failover_job")?.args, {
    p_job: id,
    p_organization: "AGENTIC-OBSERVER26-runner-2",
  });
});

Deno.test("without a healthy organization the failed job stays queued for its original organization", async () => {
  const id = "00000000-0000-4000-8000-000000000001", key = btoa("k".repeat(32));
  const encrypted = await encryptCredential("n".repeat(43), id + ":nonce", key);
  const calls: string[] = [];
  const result = await dispatchPending(
    (name) => {
      calls.push(name);
      if (name === "observer_pending_jobs") {
        return Promise.resolve([{
          id,
          kind: "prepare",
          organization: "AGENTIC-OBSERVER26-runner-1",
          workflow_sha: "a".repeat(40),
          encrypted_nonce: encrypted,
        }]);
      }
      if (name === "observer_organizations_by_load") {
        return Promise.resolve([
          { organization: "AGENTIC-OBSERVER26-runner-1", approved_sha: "a".repeat(40), over_limit: false },
          { organization: "AGENTIC-OBSERVER26-runner-2", approved_sha: "b".repeat(40), over_limit: true },
        ]);
      }
      return Promise.resolve(null);
    },
    { dispatch: () => Promise.reject(new GitHubError("organization_not_installed")) },
    key,
  );
  assertEquals(result, [{ id, dispatched: false, error: "organization_not_installed" }]);
  assertEquals(calls.filter((c) => c === "observer_failover_job"), []);
});

type Dispatch = { org: string; sha: string; runner?: string };

async function fallbackScenario(
  job: Record<string, unknown>,
  options: { slot: boolean; fail?: (org: string, runner?: string) => GitHubError | null },
) {
  const id = "00000000-0000-4000-8000-000000000001", key = btoa("k".repeat(32)), nonce = "n".repeat(43);
  const encrypted = await encryptCredential(nonce, id + ":nonce", key);
  const calls: { name: string; args: unknown }[] = [];
  const dispatches: Dispatch[] = [];
  const result = await dispatchPending(
    (name, args) => {
      calls.push({ name, args });
      if (name === "observer_pending_jobs") {
        return Promise.resolve([{
          id,
          kind: "execute",
          organization: "AGENTIC-OBSERVER26-runner-1",
          workflow_sha: "a".repeat(40),
          encrypted_nonce: encrypted,
          runner: "github-hosted",
          fallback: null,
          ...job,
        }]);
      }
      if (name === "observer_organizations_by_load") {
        return Promise.resolve([
          { organization: "AGENTIC-OBSERVER26-runner-1", approved_sha: "a".repeat(40), over_limit: false },
          { organization: "AGENTIC-OBSERVER26-runner-2", approved_sha: "b".repeat(40), over_limit: false },
        ]);
      }
      if (name === "observer_fallback_job") {
        return options.slot
          ? Promise.resolve({ organization: "AGENTIC-OBSERVER26-runner-13", approved_sha: "f".repeat(40) })
          : Promise.reject(new Error("fallback_unavailable"));
      }
      return Promise.resolve(null);
    },
    {
      dispatch: (org, _workflow, _job, token, sha, runner?: string) => {
        assertEquals(token, nonce);
        dispatches.push(runner ? { org, sha, runner } : { org, sha });
        const error = options.fail?.(org, runner);
        return error ? Promise.reject(error) : Promise.resolve();
      },
    },
    key,
  );
  return { id, result, calls, dispatches };
}

Deno.test("when every GitHub-hosted organization fails the job falls back to the self-hosted runner", async () => {
  const { id, result, calls, dispatches } = await fallbackScenario({}, {
    slot: true,
    fail: (_org, runner) => runner ? null : new GitHubError("github_request_failed", 403),
  });
  assertEquals(result, [{ id, dispatched: true, fallback: "AGENTIC-OBSERVER26-runner-13" }]);
  assertEquals(dispatches, [
    { org: "AGENTIC-OBSERVER26-runner-1", sha: "a".repeat(40) },
    { org: "AGENTIC-OBSERVER26-runner-2", sha: "b".repeat(40) },
    { org: "AGENTIC-OBSERVER26-runner-13", sha: "f".repeat(40), runner: "self-hosted" },
  ]);
  // Organizations that just failed are never chosen as the fallback.
  assertEquals(calls.find((c) => c.name === "observer_fallback_job")?.args, {
    p_job: id,
    p_avoid: ["AGENTIC-OBSERVER26-runner-1", "AGENTIC-OBSERVER26-runner-2"],
  });
});

Deno.test("a healthy GitHub-hosted organization is always preferred over the fallback", async () => {
  const { id, result, calls } = await fallbackScenario({}, {
    slot: true,
    fail: (org) => org.endsWith("-1") ? new GitHubError("github_request_failed", 403) : null,
  });
  assertEquals(result, [{ id, dispatched: true, failover: "AGENTIC-OBSERVER26-runner-2" }]);
  assertEquals(calls.filter((c) => c.name === "observer_fallback_job"), []);
});

Deno.test("transient GitHub errors and a full fallback never move the job to the self-hosted runner", async () => {
  const transient = await fallbackScenario({}, { slot: true, fail: () => new GitHubError("github_unavailable") });
  assertEquals(transient.result, [{ id: transient.id, dispatched: false, error: "github_unavailable" }]);
  assertEquals(transient.calls.filter((c) => c.name === "observer_fallback_job"), []);
  const full = await fallbackScenario({}, {
    slot: false,
    fail: () => new GitHubError("organization_not_installed"),
  });
  assertEquals(full.result, [{ id: full.id, dispatched: false, error: "organization_not_installed" }]);
  assertEquals(full.dispatches.filter((d) => d.runner), []);
});

Deno.test("stalled and over-limit jobs go to a free fallback slot first", async () => {
  for (const reason of ["stalled", "over_limit"]) {
    const { id, result, dispatches } = await fallbackScenario({ fallback: reason }, { slot: true });
    assertEquals(result, [{ id, dispatched: true, fallback: "AGENTIC-OBSERVER26-runner-13" }]);
    assertEquals(dispatches, [{ org: "AGENTIC-OBSERVER26-runner-13", sha: "f".repeat(40), runner: "self-hosted" }]);
  }
  // The slot was taken in the meantime: a stalled job is not dispatched a fourth
  // time, an over-limit job still runs GitHub-hosted in its own organization.
  const stalled = await fallbackScenario({ fallback: "stalled" }, { slot: false });
  assertEquals(stalled.result, [{ id: stalled.id, dispatched: false, error: "github_run_not_started" }]);
  assertEquals(stalled.dispatches, []);
  const over = await fallbackScenario({ fallback: "over_limit" }, { slot: false });
  assertEquals(over.result, [{ id: over.id, dispatched: true }]);
  assertEquals(over.dispatches, [{ org: "AGENTIC-OBSERVER26-runner-1", sha: "a".repeat(40) }]);
});

Deno.test("a self-hosted job is re-dispatched to its runner pool and never fails over", async () => {
  const { id, result, dispatches, calls } = await fallbackScenario({
    organization: "AGENTIC-OBSERVER26-runner-13",
    workflow_sha: "f".repeat(40),
    runner: "self-hosted",
  }, { slot: true, fail: () => new GitHubError("github_request_failed", 403) });
  assertEquals(result, [{ id, dispatched: false, error: "github_request_failed" }]);
  assertEquals(dispatches, [{ org: "AGENTIC-OBSERVER26-runner-13", sha: "f".repeat(40), runner: "self-hosted" }]);
  assertEquals(calls.filter((c) => c.name === "observer_failover_job" || c.name === "observer_fallback_job"), []);
});

Deno.test("a split run's partner moved along with its job waits for the next round", async () => {
  const key = btoa("k".repeat(32)), nonce = "n".repeat(43);
  const ids = ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"];
  const jobs = await Promise.all(ids.map(async (id, n) => ({
    id,
    kind: n ? "execute" : "engine",
    organization: "AGENTIC-OBSERVER26-runner-1",
    workflow_sha: "a".repeat(40),
    encrypted_nonce: await encryptCredential(nonce, id + ":nonce", key),
    runner: "github-hosted",
    fallback: "over_limit",
  })));
  const dispatches: string[] = [];
  const result = await dispatchPending(
    (name) => {
      if (name === "observer_pending_jobs") return Promise.resolve(jobs);
      if (name === "observer_fallback_job") {
        return Promise.resolve({
          organization: "AGENTIC-OBSERVER26-runner-13",
          approved_sha: "f".repeat(40),
          partner: ids[1],
        });
      }
      return Promise.resolve(null);
    },
    {
      dispatch: (org, _workflow, job, _nonce, _sha, runner?: string) => {
        dispatches.push(job + "@" + org + (runner ? ":" + runner : ""));
        return Promise.resolve();
      },
    },
    key,
  );
  assertEquals(result, [
    { id: ids[0], dispatched: true, fallback: "AGENTIC-OBSERVER26-runner-13" },
    { id: ids[1], dispatched: false, error: "moved_to_fallback" },
  ]);
  // The partner is never dispatched GitHub-hosted from its stale row.
  assertEquals(dispatches, [ids[0] + "@AGENTIC-OBSERVER26-runner-13:self-hosted"]);
});

const PUBLIC_JOB = "00000000-0000-4000-8000-0000000000aa";
const POOL = { organization: "AGENTIC-OBSERVER26-runner-12", approved_sha: "b".repeat(40), repository_id: "42" };

function publicRpc(
  job: Record<string, unknown>,
  overrides: Record<string, (args: Record<string, unknown>) => unknown> = {},
) {
  const calls: { name: string; args: Record<string, unknown> }[] = [];
  const rpc = (name: string, args: Record<string, unknown>) => {
    calls.push({ name, args });
    if (overrides[name]) return Promise.resolve().then(() => overrides[name](args));
    if (name === "observer_pending_jobs") return Promise.resolve([job]);
    return Promise.resolve(null);
  };
  return { rpc, calls };
}

Deno.test("a public-pool job is dispatched with its job id only and bound to the returned run", async () => {
  const { rpc, calls } = publicRpc(
    {
      id: PUBLIC_JOB,
      kind: "engine",
      organization: "AGENTIC-OBSERVER26-runner-3",
      workflow_sha: "a".repeat(40),
      encrypted_nonce: "not decrypted",
      runner: "github-hosted",
      public: true,
    },
    { observer_public_job: () => POOL },
  );
  const dispatched: unknown[] = [];
  const result = await dispatchPending(rpc, {
    dispatch: () => Promise.reject(new Error("private dispatch must not run")),
    dispatchPublic: (...args) => {
      dispatched.push(args);
      return Promise.resolve("987654");
    },
  }, btoa("k".repeat(32)));
  assertEquals(result, [{ id: PUBLIC_JOB, dispatched: true, public: POOL.organization }]);
  assertEquals(dispatched, [[POOL.organization, "42", PUBLIC_JOB, POOL.approved_sha, "engine"]]);
  assertEquals(calls.at(-1), {
    name: "observer_mark_public_dispatched",
    args: { p_job: PUBLIC_JOB, p_github_run: "987654" },
  });
});

Deno.test("a failed public dispatch sends the job home for good and dispatches it there at once", async () => {
  const key = btoa("k".repeat(32)), nonce = "n".repeat(43);
  const { rpc, calls } = publicRpc(
    {
      id: PUBLIC_JOB,
      kind: "engine",
      organization: "AGENTIC-OBSERVER26-runner-3",
      workflow_sha: "a".repeat(40),
      encrypted_nonce: await encryptCredential(nonce, PUBLIC_JOB + ":nonce", key),
      runner: "github-hosted",
      public: true,
    },
    { observer_public_job: () => POOL },
  );
  const result = await dispatchPending(rpc, {
    dispatch: (org, _workflow, _job, token) => {
      assertEquals([org, token], ["AGENTIC-OBSERVER26-runner-3", nonce]);
      return Promise.resolve();
    },
    dispatchPublic: () => Promise.reject(new GitHubError("github_request_failed", 422)),
  }, key);
  assertEquals(result, [{ id: PUBLIC_JOB, dispatched: true }]);
  assertEquals(calls.map((c) => c.name).slice(-3), [
    "observer_public_job",
    "observer_public_return_job",
    "observer_mark_dispatched",
  ]);
  assertEquals(calls.find((c) => c.name === "observer_public_return_job")?.args, {
    p_job: PUBLIC_JOB,
    p_error: "github_request_failed",
  });
});

Deno.test("when every organization fails and the public pool fails too, the self-hosted fallback is still tried", async () => {
  const key = btoa("k".repeat(32)), nonce = "n".repeat(43);
  const { rpc, calls } = publicRpc(
    {
      id: PUBLIC_JOB,
      kind: "engine",
      organization: "AGENTIC-OBSERVER26-runner-3",
      workflow_sha: "a".repeat(40),
      encrypted_nonce: await encryptCredential(nonce, PUBLIC_JOB + ":nonce", key),
      runner: "github-hosted",
    },
    {
      observer_organizations_by_load: () => [],
      observer_public_job: () => POOL,
      observer_fallback_job: () => ({ organization: "AGENTIC-OBSERVER26-runner-13", approved_sha: "c".repeat(40) }),
    },
  );
  const result = await dispatchPending(rpc, {
    dispatch: (org) =>
      org === "AGENTIC-OBSERVER26-runner-13"
        ? Promise.resolve()
        : Promise.reject(new GitHubError("organization_not_installed")),
    dispatchPublic: () => Promise.reject(new GitHubError("github_request_failed", 422)),
  }, key);
  assertEquals(result, [{ id: PUBLIC_JOB, dispatched: true, fallback: "AGENTIC-OBSERVER26-runner-13" }]);
  assert(calls.some((c) => c.name === "observer_public_return_job"));
});

Deno.test("a broken public-pool reconcile never stops private dispatching", async () => {
  const key = btoa("k".repeat(32)), nonce = "n".repeat(43);
  const { rpc } = publicRpc(
    {
      id: PUBLIC_JOB,
      kind: "engine",
      organization: "AGENTIC-OBSERVER26-runner-3",
      workflow_sha: "a".repeat(40),
      encrypted_nonce: await encryptCredential(nonce, PUBLIC_JOB + ":nonce", key),
      runner: "github-hosted",
    },
    {
      observer_public_pool_reconcile: () => {
        throw new Error("function does not exist");
      },
    },
  );
  const result = await dispatchPending(rpc, { dispatch: () => Promise.resolve() }, key);
  assertEquals(result, [{ id: PUBLIC_JOB, dispatched: true }]);
});

Deno.test("when the public pool declines, the job is dispatched to its own organization", async () => {
  const key = btoa("k".repeat(32)), nonce = "n".repeat(43);
  const { rpc, calls } = publicRpc(
    {
      id: PUBLIC_JOB,
      kind: "engine",
      organization: "AGENTIC-OBSERVER26-runner-3",
      workflow_sha: "a".repeat(40),
      encrypted_nonce: await encryptCredential(nonce, PUBLIC_JOB + ":nonce", key),
      runner: "github-hosted",
      public: true,
    },
    {
      observer_public_job: () => {
        throw new Error("public_pool_unavailable");
      },
    },
  );
  const result = await dispatchPending(rpc, {
    dispatch: (org) => {
      assertEquals(org, "AGENTIC-OBSERVER26-runner-3");
      return Promise.resolve();
    },
    dispatchPublic: () => Promise.reject(new Error("public dispatch must not run")),
  }, key);
  assertEquals(result, [{ id: PUBLIC_JOB, dispatched: true }]);
  assertEquals(calls.at(-1)?.name, "observer_mark_dispatched");
});

Deno.test("a queued public job whose pool was switched off returns home", async () => {
  const { rpc, calls } = publicRpc(
    {
      id: PUBLIC_JOB,
      kind: "engine",
      organization: POOL.organization,
      workflow_sha: POOL.approved_sha,
      encrypted_nonce: "x",
      runner: "public-hosted",
      public: null,
    },
  );
  const result = await dispatchPending(rpc, {
    dispatch: () => Promise.reject(new Error("no private dispatch")),
    dispatchPublic: () => Promise.reject(new Error("no public dispatch")),
  }, btoa("k".repeat(32)));
  assertEquals(result, [{ id: PUBLIC_JOB, dispatched: false, error: "public_pool_unavailable" }]);
  assertEquals(calls.map((c) => c.name).slice(-2), ["observer_public_target", "observer_public_return_job"]);
});
