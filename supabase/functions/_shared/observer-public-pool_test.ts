import { assertEquals, assertRejects } from "@std/assert";
import { encryptCredential, ProxyError } from "./observer-model.ts";
import { jobRequest } from "./observer-job.ts";
import type { JobDependencies, PublicPoolDependencies } from "./observer-job.ts";
import { decodeKey, encodeKey, openSealed, publicKeyFor, seal, SealError } from "./observer-seal.ts";
import { readZipFiles, singleFileZip } from "./observer-zip.ts";
import { GitHubError } from "./observer-github.ts";

const job = "00000000-0000-4000-8000-000000000001";
const run = "00000000-0000-4000-8000-000000000002";
const user = "00000000-0000-4000-8000-000000000003";
const master = btoa("k".repeat(32));
const resultKey = crypto.getRandomValues(new Uint8Array(32));
const runnerKey = crypto.getRandomValues(new Uint8Array(32));
const credential = "obs_" + run + "." + "s".repeat(43);

const identity = {
  organization: "AGENTIC-OBSERVER26-runner-12",
  organizationId: "101",
  repositoryId: "42",
  approvedSha: "a".repeat(40),
  workflow: "observer-engine.yml",
  repository: "observer-public",
  visibility: "public",
  runId: null,
  runAttempt: null,
};
const stored = {
  kind: "engine",
  job_id: job,
  run_id: run,
  run_credential: "obs_" + run + "." + "e".repeat(43),
  session_url: "https://platform.test/functions/v1/observer-session",
  scenario_ref: { bucket: "observer-scenarios", path: "card/scenario.zip" },
  scenario_digest: "d".repeat(64),
  runtime_seconds: 300,
  artifact_upload: { kind: "github" },
  restricted_egress: true,
  archive_ref: "github:AGENTIC-OBSERVER26-runner-3/participant-" + "f".repeat(32) + "@" + "c".repeat(40),
  colocated: {
    run_credential: credential,
    model_base_url: "https://platform.test/functions/v1/observer-model/v1",
    source_digest: "b".repeat(64),
    manifest: { schema_version: "observer-project-v1", image: "python@sha256:" + "c".repeat(64), run: ["python3"] },
  },
};

function request(action: string, extra: Record<string, unknown> = {}) {
  return new Request("https://platform.test/observer-job", {
    method: "POST",
    headers: { authorization: "Bearer signed.github.token", "content-type": "application/json" },
    body: JSON.stringify({ action, job_id: job, ...extra }),
  });
}

function deps(overrides: Partial<PublicPoolDependencies> = {}, input: Record<string, unknown> = stored) {
  const staged = new Map<string, Uint8Array>();
  const calls: { name: string; args: Record<string, unknown> }[] = [];
  const pool: PublicPoolDependencies = {
    resultKey,
    readScenario: () => Promise.resolve(new TextEncoder().encode("hidden scenario bytes")),
    readArchive: () => Promise.resolve(new TextEncoder().encode("participant project bytes")),
    stage: (id, name, sealed) => {
      staged.set(name, sealed);
      return Promise.resolve("https://storage.test/sealed/" + id + "/" + name + ".zip?token=t");
    },
    resultUpload: (id) =>
      Promise.resolve({
        url: "https://storage.test/upload/sealed/" + id + "/result.zip?token=u",
        path: "sealed/" + id + "/result.zip",
      }),
    readResult: () => Promise.reject(new Error("no result")),
    commitResult: () => Promise.reject(new Error("no commit")),
    ...overrides,
  };
  const dependencies: JobDependencies = {
    masterKey: master,
    verify: () => Promise.resolve({ runId: "777", runAttempt: "1", subject: "repo" }),
    scenarioDownload: () => Promise.reject(new Error("plain scenario URL must not be issued")),
    archiveDownload: () => Promise.reject(new Error("plain archive URL must not be issued")),
    publicPool: pool,
    rpc: async (name, args) => {
      calls.push({ name, args });
      if (name === "observer_job_identity") return identity;
      if (name === "observer_claim_job") return await encryptCredential(JSON.stringify(input), job, master);
      if (name === "observer_job_artifact_target") return { user_id: user, artifact_id: run, kind: "engine" };
      return null;
    },
  };
  return { dependencies, staged, calls };
}

Deno.test("sealing round-trips, binds its context and opens a Python-sealed object", async () => {
  const recipient = crypto.getRandomValues(new Uint8Array(32));
  const sealed = await seal(publicKeyFor(recipient), new TextEncoder().encode("result"), "result:" + job);
  assertEquals(new TextDecoder().decode(await openSealed(recipient, sealed, "result:" + job)), "result");
  await assertRejects(() => openSealed(recipient, sealed, "result:" + run), SealError);
  await assertRejects(() => openSealed(crypto.getRandomValues(new Uint8Array(32)), sealed, "result:" + job), SealError);
  const tampered = sealed.slice();
  tampered[tampered.length - 1] ^= 1;
  await assertRejects(() => openSealed(recipient, tampered, "result:" + job), SealError);
  // project_platform/sealing.py, private key bytes 1..32 (tests/test_public_runner_pool.py).
  const python = Uint8Array.from(
    atob(
      "T1NCMT0GnI1RDH9/fuqkL2fItHGzrmuy2/WXmodbQdczB/dva4LHEHIXYICae+VBWXp2/bSV0983nbwA9Nvo+H97brU=",
    ),
    (c) => c.charCodeAt(0),
  );
  const fixed = decodeKey("AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA");
  assertEquals(encodeKey(publicKeyFor(fixed)), "B6N8vBQgk8i3VdwbEOhstCY3StFqqFPtC9_AsrhtHHw");
  assertEquals(new TextDecoder().decode(await openSealed(fixed, python, "result:" + job)), "sealed by python");
});

Deno.test("a public-pool claim needs no nonce and returns only ciphertext", async () => {
  const { dependencies, staged, calls } = deps();
  const response = await jobRequest(request("claim", { runner_key: encodeKey(publicKeyFor(runnerKey)) }), dependencies);
  assertEquals(Object.keys(response), ["sealed"]);
  const claim = calls.find((c) => c.name === "observer_claim_job")!;
  assertEquals(claim.args.p_nonce, null);
  assertEquals(claim.args.p_github_run, "777");
  const sealed = Uint8Array.from(atob((response as { sealed: string }).sealed), (c) => c.charCodeAt(0));
  const payload = JSON.parse(new TextDecoder().decode(await openSealed(runnerKey, sealed, "claim:" + job)));
  assertEquals(payload.scenario_url, "https://storage.test/sealed/" + job + "/scenario.zip?token=t");
  assertEquals(payload.archive_url, "https://storage.test/sealed/" + job + "/project.zip?token=t");
  // The upload URL is issued only when the result is ready (result_upload).
  assertEquals(payload.artifact_upload, { kind: "sealed", path: "sealed/" + job + "/result.zip" });
  assertEquals(payload.result_key, encodeKey(publicKeyFor(resultKey)));
  assertEquals(payload.scenario_ref, undefined);
  assertEquals(payload.archive_ref, undefined);
  // Staged inputs are sealed to this runner's key, each for its own purpose.
  assertEquals(
    new TextDecoder().decode(await openSealed(runnerKey, staged.get("scenario")!, "scenario:" + job)),
    "hidden scenario bytes",
  );
  assertEquals(
    new TextDecoder().decode(await openSealed(runnerKey, staged.get("project")!, "project:" + job)),
    "participant project bytes",
  );
  await assertRejects(() => openSealed(runnerKey, staged.get("scenario")!, "project:" + job), SealError);
});

Deno.test("a public-pool claim refuses nonces, missing runner keys and non-colocated jobs", async () => {
  const key = encodeKey(publicKeyFor(runnerKey));
  for (
    const [extra, input] of [
      [{ runner_key: key, nonce: "n".repeat(43) }, stored],
      [{}, stored],
      [{ runner_key: "short" }, stored],
      [{ runner_key: key }, { ...stored, colocated: undefined, archive_ref: undefined, restricted_egress: undefined }],
    ] as [Record<string, unknown>, Record<string, unknown>][]
  ) {
    const { dependencies } = deps({}, input);
    await assertRejects(() => jobRequest(request("claim", extra), dependencies), ProxyError);
  }
  // Without the configured sealing key the public pool is refused outright.
  const { dependencies } = deps();
  await assertRejects(
    () => jobRequest(request("claim", { runner_key: key }), { ...dependencies, publicPool: undefined }),
    ProxyError,
    "public_pool_action_denied",
  );
  for (const action of ["artifact_repository", "agent_log"]) {
    await assertRejects(() => jobRequest(request(action, { runner_key: key }), dependencies), ProxyError);
  }
});

Deno.test("a sealed result is opened by the backend and committed for the claimed run only", async () => {
  const archive = await singleFileZip("decisions.csv", new TextEncoder().encode("step,action\n1,wait\n"));
  const committed: unknown[] = [];
  const { dependencies } = deps({
    readResult: async () => await seal(publicKeyFor(resultKey), archive, "result:" + job),
    commitResult: (u, r, files) => {
      committed.push([u, r, files.map((f) => [f.path, new TextDecoder().decode(f.data), f.executable])]);
      return Promise.resolve("github:AGENTIC-OBSERVER26-runner-3/participant-" + "f".repeat(32) + "@" + "e".repeat(40));
    },
  });
  assertEquals(await jobRequest(request("store_result"), dependencies), {
    result_path: "github:AGENTIC-OBSERVER26-runner-3/participant-" + "f".repeat(32) + "@" + "e".repeat(40),
  });
  assertEquals(committed, [[user, run, [["decisions.csv", "step,action\n1,wait\n", false]]]]);
  // A result sealed for another job, or not sealed at all, is rejected.
  for (
    const blob of [
      await seal(publicKeyFor(resultKey), archive, "result:" + run),
      archive,
      await seal(publicKeyFor(resultKey), new TextEncoder().encode("not a zip"), "result:" + job),
    ]
  ) {
    const { dependencies } = deps({ readResult: () => Promise.resolve(blob), commitResult: () => Promise.reject() });
    await assertRejects(() => jobRequest(request("store_result"), dependencies), ProxyError, "invalid_job_result");
  }
});

Deno.test("a failed result commit is reported as retryable, never as GitHub's status", async () => {
  const archive = await singleFileZip("decisions.csv", new TextEncoder().encode("x"));
  const { dependencies } = deps({
    readResult: async () => await seal(publicKeyFor(resultKey), archive, "result:" + job),
    commitResult: () => Promise.reject(new GitHubError("github_request_failed", 500)),
  });
  const error = await assertRejects(() => jobRequest(request("store_result"), dependencies), ProxyError);
  assertEquals([error.status, error.code], [503, "result_commit_unavailable"]);
});

Deno.test("a fresh result upload URL is issued only to the run holding the claim", async () => {
  const { dependencies } = deps();
  assertEquals(await jobRequest(request("result_upload"), dependencies), {
    url: "https://storage.test/upload/sealed/" + job + "/result.zip?token=u",
    path: "sealed/" + job + "/result.zip",
  });
  const rpc = dependencies.rpc;
  dependencies.rpc = (name, args) =>
    name === "observer_job_artifact_target"
      ? Promise.reject(new ProxyError(409, "artifact_access_denied"))
      : rpc(name, args);
  await assertRejects(() => jobRequest(request("result_upload"), dependencies), ProxyError, "artifact_access_denied");
});

Deno.test("store_result is refused for private-pool jobs", async () => {
  const { dependencies } = deps();
  const rpc = dependencies.rpc;
  dependencies.rpc = (name, args) =>
    name === "observer_job_identity"
      ? Promise.resolve({ ...identity, repository: "observer-control", visibility: "private", repositoryId: "1" })
      : rpc(name, args);
  await assertRejects(() => jobRequest(request("store_result"), dependencies), ProxyError, "artifact_access_denied");
  await assertRejects(() => jobRequest(request("result_upload"), dependencies), ProxyError, "artifact_access_denied");
});

Deno.test("result archives with unsafe paths are refused", async () => {
  assertEquals((await readZipFiles(singleFileZipSync("logs/agent.log"), 1024)).map((f) => f.path), ["logs/agent.log"]);
  for (const name of ["../escape", "/abs", "a//b", ".git/config", "a\\b"]) {
    await assertRejects(() => readZipFiles(singleFileZipSync(name), 1024));
  }
});

function singleFileZipSync(name: string) {
  // Stored entry, built by hand so any name can be tested.
  const data = new TextEncoder().encode("x"), encoded = new TextEncoder().encode(name);
  const local = new Uint8Array(30 + encoded.length + 1), v = new DataView(local.buffer);
  v.setUint32(0, 0x04034b50, true);
  v.setUint16(4, 20, true);
  v.setUint32(14, 0x8cdc1683, true);
  v.setUint32(18, 1, true);
  v.setUint32(22, 1, true);
  v.setUint16(26, encoded.length, true);
  local.set(encoded, 30);
  local.set(data, 30 + encoded.length);
  const central = new Uint8Array(46 + encoded.length), c = new DataView(central.buffer);
  c.setUint32(0, 0x02014b50, true);
  c.setUint32(16, 0x8cdc1683, true);
  c.setUint32(20, 1, true);
  c.setUint32(24, 1, true);
  c.setUint16(28, encoded.length, true);
  central.set(encoded, 46);
  const end = new Uint8Array(22), e = new DataView(end.buffer);
  e.setUint32(0, 0x06054b50, true);
  e.setUint16(8, 1, true);
  e.setUint16(10, 1, true);
  e.setUint32(12, central.length, true);
  e.setUint32(16, local.length, true);
  const out = new Uint8Array(local.length + central.length + end.length);
  out.set(local);
  out.set(central, local.length);
  out.set(end, local.length + central.length);
  return out;
}

Deno.test("a public-pool score job gets its scenario and the run's result sealed, and nothing to upload", async () => {
  const scoreInput = {
    kind: "score",
    job_id: job,
    run_id: run,
    scenario_ref: { bucket: "observer-scenarios", path: "card/scenario.zip" },
    scenario_digest: "d".repeat(64),
    result_ref: "github:AGENTIC-OBSERVER26-runner-3/participant-" + "f".repeat(32) + "@" + "c".repeat(40),
    decisions_digest: "e".repeat(64),
    termination_reason: "agent_finished",
  };
  const { dependencies, staged } = deps({
    readArchive: () => Promise.resolve(new TextEncoder().encode("run result bytes")),
  }, scoreInput);
  const asScore = (d: JobDependencies): JobDependencies => ({
    ...d,
    rpc: async (name, args) =>
      name === "observer_job_identity" ? { ...identity, workflow: "observer-score.yml" } : await d.rpc(name, args),
  });
  const response = await jobRequest(
    request("claim", { runner_key: encodeKey(publicKeyFor(runnerKey)) }),
    asScore(dependencies),
  );
  assertEquals(Object.keys(response), ["sealed"]);
  const sealed = Uint8Array.from(atob((response as { sealed: string }).sealed), (c) => c.charCodeAt(0));
  const payload = JSON.parse(new TextDecoder().decode(await openSealed(runnerKey, sealed, "claim:" + job)));
  assertEquals(payload.scenario_url, "https://storage.test/sealed/" + job + "/scenario.zip?token=t");
  assertEquals(payload.result_url, "https://storage.test/sealed/" + job + "/trace.zip?token=t");
  assertEquals([payload.artifact_upload, payload.result_key, payload.result_ref], [undefined, undefined, undefined]);
  assertEquals(
    new TextDecoder().decode(await openSealed(runnerKey, staged.get("trace")!, "trace:" + job)),
    "run result bytes",
  );
  await assertRejects(() => openSealed(runnerKey, staged.get("trace")!, "scenario:" + job), SealError);
  // A score payload carrying a result key is refused.
  const forged = deps({}, { ...scoreInput, result_key: encodeKey(publicKeyFor(resultKey)) });
  await assertRejects(
    () =>
      jobRequest(request("claim", { runner_key: encodeKey(publicKeyFor(runnerKey)) }), asScore(forged.dependencies)),
    ProxyError,
  );
});

Deno.test("egress route nodes reach a public run only inside the sealed claim", async () => {
  const node = {
    type: "hysteria2",
    server: "hy1.node-canary.example",
    server_port: 40001,
    password: "hy2-password-canary",
    tls: { enabled: true, server_name: "hy1.node-canary.example" },
  };
  const input = {
    ...stored,
    restricted_egress: undefined,
    team_egress: {
      environment: {},
      secrets: [],
      domains: [],
      open: true,
      route: { name: "overseas", fallback: true, cap_bytes: 1000 },
    },
  };
  const { dependencies } = deps({}, JSON.parse(JSON.stringify(input)));
  dependencies.egressRoutes = { cn: [], overseas: [node] };
  const response = await jobRequest(request("claim", { runner_key: encodeKey(publicKeyFor(runnerKey)) }), dependencies);
  const text = JSON.stringify(response);
  for (const secret of ["node-canary", "hy2-password-canary", "40001"]) assertEquals(text.includes(secret), false);
  const sealed = Uint8Array.from(atob((response as { sealed: string }).sealed), (c) => c.charCodeAt(0));
  const payload = JSON.parse(new TextDecoder().decode(await openSealed(runnerKey, sealed, "claim:" + job)));
  assertEquals(payload.team_egress.route, { name: "overseas", fallback: true, cap_bytes: 1000, nodes: [node] });
});
