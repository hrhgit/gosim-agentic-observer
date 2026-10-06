import { assertEquals, assertRejects, assertThrows } from "@std/assert";
import { encryptCredential, ProxyError } from "./observer-model.ts";
import { jobRequest, validateJobPayload } from "./observer-job.ts";
import type { JobDependencies } from "./observer-job.ts";
import { GitHubError } from "./observer-github.ts";
import type { WorkflowIdentity } from "./observer-github.ts";

const job = "00000000-0000-4000-8000-000000000001";
const key = btoa("k".repeat(32));
const identity: WorkflowIdentity = {
  organization: "AGENTIC-OBSERVER26-runner-1",
  organizationId: "101",
  repositoryId: "303",
  approvedSha: "a".repeat(40),
  workflow: "observer-execute.yml",
};
const payload = {
  kind: "execute",
  job_id: job,
  run_id: "00000000-0000-4000-8000-000000000002",
  archive_url: "https://codeload.github.com/approved/source.zip",
  source_digest: "b".repeat(64),
  manifest: {
    schema_version: "observer-project-v1",
    image: "python@sha256:" + "c".repeat(64),
    run: ["python3", "agent.py"],
  },
  session_url: "https://platform.test/functions/v1/observer-session",
  run_credential: "obs_00000000-0000-4000-8000-000000000002." + "s".repeat(43),
  model_base_url: "https://platform.test/functions/v1/observer-model/v1",
};

function request(action = "claim", extra: Record<string, unknown> = {}) {
  return new Request("https://platform.test/observer-job", {
    method: "POST",
    headers: { authorization: "Bearer signed.github.token", "content-type": "application/json" },
    body: JSON.stringify({ action, job_id: job, nonce: "n".repeat(43), ...extra }),
  });
}

Deno.test("job payload classes cannot leak simulation or administration secrets into execution", () => {
  assertEquals(validateJobPayload(payload, identity, job), payload);
  for (
    const extra of [{ scenario_url: "https://private.test/truth" }, { service_key: "secret" }, {
      repository_token: "installation-secret",
    }, { kind: "engine" }]
  ) {
    assertThrows(() => validateJobPayload({ ...payload, ...extra }, identity, job), ProxyError);
  }
});

Deno.test("invalid payloads fail before a worker downloads or executes anything", () => {
  for (
    const change of [
      { run_credential: "obs_00000000-0000-4000-8000-000000000099." + "s".repeat(43) },
      { archive_url: "http://insecure.test/source.zip" },
      { archive_url: "https://user:password@private.test/source.zip" },
      { source_digest: "not-a-hash" },
      { manifest: { ...payload.manifest, image: "python:latest" } },
      { manifest: { ...payload.manifest, run: "python agent.py" } },
      { session_url: undefined },
    ]
  ) assertThrows(() => validateJobPayload({ ...payload, ...change }, identity, job), ProxyError);
  const engine = {
    kind: "engine",
    job_id: job,
    run_id: payload.run_id,
    run_credential: payload.run_credential,
    session_url: payload.session_url,
    scenario_url: "https://private.test/scenario.zip",
    scenario_digest: "a".repeat(64),
    runtime_seconds: 120,
    artifact_upload: { url: "https://storage.test/signed", path: job + "/" + payload.run_id + "/result.zip" },
  };
  const expected: WorkflowIdentity = { ...identity, workflow: "observer-engine.yml" };
  assertEquals(validateJobPayload(engine, expected, job), engine);
  for (
    const change of [
      { runtime_seconds: 18001 },
      { runtime_seconds: "120" },
      { scenario_digest: "bad" },
      { artifact_upload: { ...engine.artifact_upload, authorization: "service-key" } },
      { artifact_upload: { ...engine.artifact_upload, path: job + "/" + job + "/result.zip" } },
    ]
  ) assertThrows(() => validateJobPayload({ ...engine, ...change }, expected, job), ProxyError);
});

Deno.test("preparation receives only its organization repository and a bounded preview destination", () => {
  const expected: WorkflowIdentity = { ...identity, workflow: "observer-prepare.yml" };
  const input = {
    kind: "prepare",
    job_id: job,
    revision_id: payload.run_id,
    archive_url: "https://codeload.github.com/approved/source.zip",
    source_digest: null,
    repository: {
      full_name: expected.organization + "/participant-" + "a".repeat(32),
      token: "repository-scoped-token",
    },
    artifact_upload: { url: "https://storage.test/signed", path: job + "/" + payload.run_id + "/preview.zip" },
  };
  assertEquals(validateJobPayload(input, expected, job), input);
  assertEquals(
    validateJobPayload(
      { ...input, model: "test-model", model_base_url: payload.model_base_url, run_credential: payload.run_credential },
      expected,
      job,
    ).model,
    "test-model",
  );
  assertEquals(validateJobPayload({ ...input, gameplay: "v4" }, expected, job).gameplay, "v4");
  // Direct model access: the team's variables instead of the model proxy, never both.
  const team = {
    environment: { OPENAI_API_KEY: "sk-team", OPENAI_MODEL: "m" },
    secrets: ["OPENAI_API_KEY"],
    domains: ["api.example.com"],
  };
  assertEquals(validateJobPayload({ ...input, team_egress: team }, expected, job).team_egress, team);
  for (
    const change of [
      { team_egress: { ...team, model: "test-model" } },
      {
        team_egress: team,
        model: "test-model",
        model_base_url: payload.model_base_url,
        run_credential: payload.run_credential,
      },
      { team_egress: { ...team, secrets: ["MISSING"] } },
      { gameplay: "v3" },
      { gameplay: 4 },
      { repository: { ...input.repository, full_name: "AGENTIC-OBSERVER26-runner-2/participant-" + "a".repeat(32) } },
      { repository: { ...input.repository, full_name: expected.organization + "/observer-control" } },
      { repository: { ...input.repository, token: "bad\nheader" } },
      { model: "test-model" },
      { run_credential: "organizer-master-key" },
      { artifact_upload: { ...input.artifact_upload, url: "http://insecure.test/upload" } },
    ]
  ) assertThrows(() => validateJobPayload({ ...input, ...change }, expected, job), ProxyError);
});

Deno.test("added runner organizations prepare into their own repository only", () => {
  for (const organization of ["AGENTIC-OBSERVER26-runner-7", "AGENTIC-OBSERVER26-runner-12"]) {
    const expected: WorkflowIdentity = { ...identity, organization, workflow: "observer-prepare.yml" };
    const input = {
      kind: "prepare",
      job_id: job,
      revision_id: payload.run_id,
      archive_url: "https://codeload.github.com/approved/source.zip",
      source_digest: null,
      repository: { full_name: organization + "/participant-" + "a".repeat(32), token: "repository-scoped-token" },
      artifact_upload: { url: "https://storage.test/signed", path: job + "/" + payload.run_id + "/preview.zip" },
    };
    assertEquals(validateJobPayload(input, expected, job), input);
    for (const other of ["AGENTIC-OBSERVER26-runner-1", "AGENTIC-OBSERVER26-runner-36"]) {
      const moved = {
        ...input,
        repository: { ...input.repository, full_name: other + "/participant-" + "a".repeat(32) },
      };
      assertThrows(() => validateJobPayload(moved, expected, job), ProxyError);
    }
  }
});

Deno.test("job claim verifies workflow before obtaining or decrypting its input", async () => {
  const steps: string[] = [];
  const encrypted = await encryptCredential(JSON.stringify(payload), job, key);
  const deps: JobDependencies = {
    masterKey: key,
    verify: async () => {
      steps.push("verify");
      return { runId: "404", runAttempt: "1", subject: "expected" };
    },
    rpc: (name, args) => {
      steps.push(name);
      if (name === "observer_job_identity") return Promise.resolve(identity);
      assertEquals(args.p_nonce, "n".repeat(43));
      assertEquals(args.p_repository, "303");
      assertEquals(args.p_github_run, "404");
      return Promise.resolve(encrypted);
    },
  };
  assertEquals(await jobRequest(request(), deps), payload);
  assertEquals(steps, ["observer_job_identity", "verify", "observer_claim_job"]);
});

Deno.test("a rejected identity never reads the encrypted job input", async () => {
  const calls: string[] = [];
  await assertRejects(() =>
    jobRequest(request(), {
      masterKey: key,
      verify: () => Promise.reject(new GitHubError("workflow_identity_mismatch", 403)),
      rpc: (name) => {
        calls.push(name);
        return Promise.resolve(identity);
      },
    }), GitHubError);
  assertEquals(calls, ["observer_job_identity"]);
});

Deno.test("completion only writes a job receipt, never a survey score", async () => {
  const calls: string[] = [];
  assertEquals(
    await jobRequest(request("complete", { result: { score: 99999 } }), {
      masterKey: key,
      verify: () => Promise.resolve({ runId: "404", runAttempt: "1", subject: "expected" }),
      rpc: (name) => {
        calls.push(name);
        return Promise.resolve(name === "observer_job_identity" ? identity : null);
      },
    }),
    { accepted: true },
  );
  assertEquals(calls, ["observer_job_identity", "observer_finish_job"]);
});

Deno.test("participant cannot select a privileged RPC by changing job action", async () => {
  const calls: string[] = [];
  await assertRejects(() =>
    jobRequest(request("observer_settle_model"), {
      masterKey: key,
      rpc: (name) => {
        calls.push(name);
        return Promise.resolve(null);
      },
    }), ProxyError);
  assertEquals(calls, []);
});

Deno.test("only a claimed trusted preparation or engine job can mint a fresh artifact credential", async () => {
  const calls: string[] = [];
  const deps: JobDependencies = {
    masterKey: key,
    verify: () => Promise.resolve({ runId: "404", runAttempt: "1", subject: "expected" }),
    rpc: (name, args) => {
      calls.push(name);
      if (name === "observer_job_identity") return Promise.resolve({ ...identity, workflow: "observer-engine.yml" });
      assertEquals(args.p_github_run, "404");
      return Promise.resolve({ user_id: "owner", artifact_id: payload.run_id, kind: "engine" });
    },
    repositoryCredentials: async (owner, kind) => {
      assertEquals(owner, "owner");
      assertEquals(kind, "engine");
      calls.push("mint-scoped-token");
      return { full_name: "private-repository", token: "fresh-scoped-token" };
    },
  };
  assertEquals(await jobRequest(request("artifact_repository"), deps), {
    full_name: "private-repository",
    token: "fresh-scoped-token",
    artifact_id: payload.run_id,
    kind: "engine",
  });
  assertEquals(calls, ["observer_job_identity", "observer_job_artifact_target", "mint-scoped-token"]);
  calls.length = 0;
  deps.rpc = (name) => {
    calls.push(name);
    return Promise.resolve(identity);
  };
  await assertRejects(() => jobRequest(request("artifact_repository"), deps), ProxyError);
  assertEquals(calls, ["observer_job_identity"]);
});

Deno.test("immutable source downloads are signed at claim time, not left to expire in the queue", async () => {
  const source = {
    ...payload,
    archive_ref: "github:AGENTIC-OBSERVER26-runner-1/participant-" + "a".repeat(32) + "@" + "b".repeat(40),
  };
  delete (source as Partial<typeof payload>).archive_url;
  const encrypted = await encryptCredential(JSON.stringify(source), job, key);
  let refreshed = false;
  const result = await jobRequest(request(), {
    masterKey: key,
    verify: () => Promise.resolve({ runId: "404", runAttempt: "1", subject: "expected" }),
    rpc: (name) => Promise.resolve(name === "observer_job_identity" ? identity : encrypted),
    archiveDownload: async (reference, privateOnly) => {
      assertEquals(reference, source.archive_ref);
      assertEquals(privateOnly, true);
      refreshed = true;
      return payload.archive_url;
    },
  });
  assertEquals(refreshed, true);
  assertEquals(result, payload);
});

Deno.test("hidden scenario signing is delayed until the engine claims; executor never receives it", async () => {
  const input = {
    kind: "engine",
    job_id: job,
    run_id: payload.run_id,
    run_credential: payload.run_credential,
    session_url: payload.session_url,
    scenario_ref: { bucket: "observer-scenarios", path: "hidden/bundle.zip" },
    scenario_digest: "a".repeat(64),
    runtime_seconds: 120,
    artifact_upload: { kind: "github" },
  };
  let signed = 0;
  const deps: JobDependencies = {
    masterKey: key,
    verify: () => Promise.resolve({ runId: "404", runAttempt: "1", subject: "expected" }),
    rpc: async (name) =>
      name === "observer_job_identity"
        ? { ...identity, workflow: "observer-engine.yml" }
        : await encryptCredential(JSON.stringify(input), job, key),
    scenarioDownload: async (path) => {
      assertEquals(path, "hidden/bundle.zip");
      signed++;
      return "https://storage.test/fresh.zip";
    },
  };
  const result: Record<string, unknown> = await jobRequest(request(), deps);
  assertEquals(result.scenario_url, "https://storage.test/fresh.zip");
  assertEquals(result.scenario_ref, undefined);
  assertEquals(signed, 1);
  input.kind = "execute";
  await assertRejects(() => jobRequest(request(), deps), ProxyError);
  assertEquals(signed, 1);
});

Deno.test("private source ZIP signing is restricted to preparation and the immutable upload path", async () => {
  const input = {
    kind: "prepare",
    job_id: job,
    revision_id: payload.run_id,
    archive_storage_ref: { bucket: "observer-staging", path: job + "/" + payload.run_id + "/source.zip" },
    repository: { full_name: identity.organization + "/participant-" + "a".repeat(32), token: "scoped-test-token" },
    artifact_upload: { kind: "github" },
  };
  let signed = 0;
  const deps: JobDependencies = {
    masterKey: key,
    verify: () => Promise.resolve({ runId: "404", runAttempt: "1", subject: "expected" }),
    rpc: async (name) =>
      name === "observer_job_identity"
        ? { ...identity, workflow: "observer-prepare.yml" }
        : await encryptCredential(JSON.stringify(input), job, key),
    sourceDownload: async (path) => {
      assertEquals(path, input.archive_storage_ref.path);
      signed++;
      return "https://storage.test/source.zip";
    },
  };
  const result: Record<string, unknown> = await jobRequest(request(), deps);
  assertEquals(result.archive_url, "https://storage.test/source.zip");
  assertEquals(result.archive_storage_ref, undefined);
  assertEquals(signed, 1);
  input.archive_storage_ref.bucket = "observer-scenarios";
  await assertRejects(() => jobRequest(request(), deps), ProxyError);
  assertEquals(signed, 1);
});

Deno.test("a stored repository snapshot is signed from observer-sources only at its snapshot path", async () => {
  const snapshot = job + "/" + "f".repeat(40) + "-" + payload.run_id + ".zip";
  const input = {
    kind: "prepare",
    job_id: job,
    revision_id: payload.run_id,
    archive_storage_ref: { bucket: "observer-sources", path: snapshot },
    repository: { full_name: identity.organization + "/participant-" + "a".repeat(32), token: "scoped-test-token" },
    artifact_upload: { kind: "github" },
  };
  const signed: [string, string | undefined][] = [];
  const deps: JobDependencies = {
    masterKey: key,
    verify: () => Promise.resolve({ runId: "404", runAttempt: "1", subject: "expected" }),
    rpc: async (name) =>
      name === "observer_job_identity"
        ? { ...identity, workflow: "observer-prepare.yml" }
        : await encryptCredential(JSON.stringify(input), job, key),
    sourceDownload: (path, bucket) => {
      signed.push([path, bucket]);
      return Promise.resolve("https://storage.test/snapshot.zip");
    },
  };
  const result: Record<string, unknown> = await jobRequest(request(), deps);
  assertEquals(result.archive_url, "https://storage.test/snapshot.zip");
  assertEquals(signed, [[snapshot, "observer-sources"]]);
  // A staging-style path in the sources bucket (or the reverse) is refused.
  input.archive_storage_ref.path = job + "/" + payload.run_id + "/source.zip";
  await assertRejects(() => jobRequest(request(), deps), ProxyError);
  assertEquals(signed.length, 1);
});

Deno.test("a colocated engine job carries only the execute job's own fields", () => {
  const expected: WorkflowIdentity = { ...identity, workflow: "observer-engine.yml" };
  const engine = {
    kind: "engine",
    job_id: job,
    run_id: payload.run_id,
    run_credential: "obs_" + payload.run_id + "." + "e".repeat(43),
    session_url: payload.session_url,
    scenario_url: "https://private.test/scenario.zip",
    scenario_digest: "a".repeat(64),
    runtime_seconds: 120,
    artifact_upload: { kind: "github" },
    archive_url: payload.archive_url,
    colocated: {
      run_credential: payload.run_credential,
      model_base_url: payload.model_base_url,
      source_digest: payload.source_digest,
      manifest: payload.manifest,
    },
  };
  assertEquals(validateJobPayload(engine, expected, job), engine);
  for (
    const change of [
      { colocated: { ...engine.colocated, run_credential: engine.run_credential } },
      { colocated: { ...engine.colocated, extra: "x" } },
      { colocated: { ...engine.colocated, manifest: { ...payload.manifest, image: "python:latest" } } },
      { colocated: undefined },
      { instance: { seed: "f".repeat(64) } },
    ]
  ) assertThrows(() => validateJobPayload({ ...engine, ...change }, expected, job), ProxyError);
});

Deno.test("only the claimed executor stores a log, for the run in its own encrypted input", async () => {
  const encrypted = await encryptCredential(JSON.stringify(payload), job, key);
  const stored: [string, string][] = [];
  const calls: string[] = [];
  const deps: JobDependencies = {
    masterKey: key,
    verify: async () => {
      calls.push("verify");
      return { runId: "404", runAttempt: "1", subject: "expected" };
    },
    rpc: (name, args) => {
      calls.push(name);
      if (name === "observer_job_identity") return Promise.resolve(identity);
      assertEquals([args.p_nonce, args.p_github_run, args.p_attempt], ["n".repeat(43), "404", "1"]);
      return Promise.resolve(encrypted);
    },
    storeAgentLog: async (run, log) => {
      stored.push([run, new TextDecoder().decode(log)]);
    },
  };
  const text = "Traceback: boom\ncredential " + payload.run_credential + "\n";
  // A run chosen in the request is ignored; the claimed job decides the run.
  const body = { log: btoa(text), run_id: "00000000-0000-4000-8000-00000000dead" };
  assertEquals(await jobRequest(request("agent_log", body), deps), { accepted: true });
  assertEquals(calls, ["observer_job_identity", "verify", "observer_claim_job"]);
  assertEquals(stored, [[payload.run_id, "Traceback: boom\ncredential [REDACTED]\n"]]);

  // Engine and preparation jobs have no participant output to store.
  for (const workflow of ["observer-engine.yml", "observer-prepare.yml"]) {
    calls.length = 0;
    const other = {
      ...deps,
      rpc: (name: string) => {
        calls.push(name);
        return Promise.resolve(name === "observer_job_identity" ? { ...identity, workflow } : encrypted);
      },
    };
    await assertRejects(() => jobRequest(request("agent_log", body), other), ProxyError, "agent_log_denied");
    assertEquals(calls, ["observer_job_identity", "verify"]);
  }
  // A job whose claim is gone (finished, or another GitHub run) stores nothing.
  const finished = {
    ...deps,
    rpc: (name: string) =>
      name === "observer_job_identity"
        ? Promise.resolve(identity)
        : Promise.reject(new ProxyError(409, "job_unavailable")),
  };
  await assertRejects(() => jobRequest(request("agent_log", body), finished), ProxyError, "job_unavailable");
  // An engine payload replayed through the executor workflow is refused.
  const engine = await encryptCredential(JSON.stringify({ ...payload, kind: "engine" }), job, key);
  const mismatched = {
    ...deps,
    rpc: (name: string) => Promise.resolve(name === "observer_job_identity" ? identity : engine),
  };
  await assertRejects(() => jobRequest(request("agent_log", body), mismatched), ProxyError, "agent_log_denied");
  await assertRejects(
    () => jobRequest(request("agent_log", { log: "not base64!" }), deps),
    ProxyError,
    "invalid_agent_log",
  );
  const huge = btoa("x".repeat(2 * 1024 * 1024 + 65537));
  await assertRejects(() => jobRequest(request("agent_log", { log: huge }), deps), ProxyError, "invalid_agent_log");
  assertEquals(stored.length, 1);
});

Deno.test("only the participant log may exceed the receipt size", async () => {
  const deps: JobDependencies = {
    masterKey: key,
    verify: () => Promise.resolve({ runId: "404", runAttempt: "1", subject: "expected" }),
    rpc: () => Promise.resolve(identity),
  };
  await assertRejects(
    () => jobRequest(request("complete", { result: { padding: "x".repeat(1200000) } }), deps),
    ProxyError,
    "body_too_large",
  );
});

Deno.test("a score job claims fresh downloads of the scenario and the run's private result only", async () => {
  const result = "github:AGENTIC-OBSERVER26-runner-1/participant-" + "a".repeat(32) + "@" + "b".repeat(40);
  const input: Record<string, unknown> = {
    kind: "score",
    job_id: job,
    run_id: payload.run_id,
    scenario_ref: { bucket: "observer-scenarios", path: "cards/v4-a.zip" },
    scenario_digest: "a".repeat(64),
    result_ref: result,
    decisions_digest: "d".repeat(64),
    termination_reason: "agent_finished",
  };
  const signed: string[] = [];
  const deps: JobDependencies = {
    masterKey: key,
    verify: () => Promise.resolve({ runId: "404", runAttempt: "1", subject: "expected" }),
    rpc: async (name) =>
      name === "observer_job_identity"
        ? { ...identity, workflow: "observer-score.yml" }
        : await encryptCredential(JSON.stringify(input), job, key),
    scenarioDownload: (path) => {
      signed.push("scenario:" + path);
      return Promise.resolve("https://storage.test/card.zip");
    },
    archiveDownload: (reference, privateOnly) => {
      assertEquals(privateOnly, true);
      signed.push("result:" + reference);
      return Promise.resolve("https://codeload.github.com/result.zip");
    },
  };
  assertEquals(await jobRequest(request(), deps), {
    kind: "score",
    job_id: job,
    run_id: payload.run_id,
    scenario_url: "https://storage.test/card.zip",
    scenario_digest: "a".repeat(64),
    result_url: "https://codeload.github.com/result.zip",
    decisions_digest: "d".repeat(64),
    termination_reason: "agent_finished",
  });
  assertEquals(signed, ["scenario:cards/v4-a.zip", "result:" + result]);
  // A result reference is resolved for a score job only.
  input.kind = "engine";
  await assertRejects(() => jobRequest(request(), deps), ProxyError);
  // A score job cannot mint repository credentials or store a participant log.
  input.kind = "score";
  await assertRejects(() =>
    jobRequest(request("artifact_repository"), {
      ...deps,
      repositoryCredentials: () => Promise.resolve({ full_name: "x/y", token: "t" }),
    }), ProxyError);
});

Deno.test("an egress route gets its nodes at claim time only, for execution jobs only", async () => {
  const { parseEgressRoutes } = await import("./observer-job.ts");
  const vless = {
    type: "vless",
    server: "cn.node-canary.example",
    server_port: 34567,
    uuid: "11111111-2222-3333-4444-555555555555",
    flow: "xtls-rprx-vision",
    tls: { enabled: true, server_name: "www.example.com", reality: { enabled: true, public_key: "k", short_id: "s" } },
  };
  const routes = parseEgressRoutes(JSON.stringify({ cn: [vless], overseas: [] }));
  const team = {
    environment: {},
    secrets: [],
    domains: [],
    open: true,
    route: { name: "cn", fallback: false, cap_bytes: 5 },
  };
  const claim = async (input: Record<string, unknown>, egressRoutes: typeof routes | null = routes) =>
    await jobRequest(request(), {
      masterKey: key,
      egressRoutes: egressRoutes ?? undefined,
      verify: () => Promise.resolve({ runId: "404", runAttempt: "1", subject: "expected" }),
      rpc: async (name) =>
        name === "observer_job_identity" ? identity : await encryptCredential(JSON.stringify(input), job, key),
    }) as Record<string, Record<string, unknown>>;
  const claimed = await claim({ ...payload, team_egress: team });
  assertEquals(claimed.team_egress.route, { ...team.route, nodes: [vless] });
  // Not configured: no node; the runner falls back or refuses as the team chose.
  assertEquals((await claim({ ...payload, team_egress: team }, null)).team_egress.route, {
    ...team.route,
    nodes: [],
  });
  // A stored input never carries nodes, and only execution jobs take a route.
  await assertRejects(
    () => claim({ ...payload, team_egress: { ...team, route: { ...team.route, nodes: [vless] } } }),
    ProxyError,
  );
  await assertRejects(
    () => claim({ ...payload, team_egress: { ...team, route: { ...team.route, name: "direct" } } }),
    ProxyError,
  );
  await assertRejects(() => claim({ ...payload, team_egress: { ...team, open: undefined } }), ProxyError);
  for (
    const bad of [
      { cn: [vless, vless] },
      { overseas: [{ ...vless, type: "socks" }] },
      { overseas: [{ ...vless, extra: 1 }] },
      { cn: [{ ...vless, server_port: 0 }] },
      { cn: [{ ...vless, tls: { enabled: false } }] },
      [],
    ]
  ) assertThrows(() => parseEgressRoutes(JSON.stringify(bad)));
});
