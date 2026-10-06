import { assert, assertEquals, assertRejects, assertThrows } from "@std/assert";
import { createLocalJWKSet, errors, exportJWK, exportPKCS8, generateKeyPair, SignJWT } from "npm:jose@6.1.0";
import {
  CONTROL_REPOSITORY,
  GitHubApp,
  GitHubError,
  isRunnerOrganization,
  parseSourceUrl,
  placement,
  PUBLIC_POOL_REPOSITORY,
  sourceRef,
  sourceRepository,
  sourceSubdir,
  verifyWorkflowIdentity,
} from "./observer-github.ts";
import type { WorkflowIdentity } from "./observer-github.ts";

const user = "00000000-0000-4000-8000-000000000001";
const locate = () => Promise.resolve("AGENTIC-OBSERVER26-runner-12");
const assigned = await placement(user, locate);
const organization = assigned.organization;
const pair = await generateKeyPair("RS256", { extractable: true });
const pem = await exportPKCS8(pair.privateKey);
const publicJwk = { ...await exportJWK(pair.publicKey), kid: "test-key", alg: "RS256" };
const keys = createLocalJWKSet({ keys: [publicJwk] });
const sha = "a".repeat(40);

function backend() {
  const calls: { method: string; path: string; body: any; authorization: string }[] = [];
  const repos = new Map<string, any>();
  const permissions = new Map<string, boolean>();
  const refs = new Map<string, string>();
  // Named branches, tags and commits of public source repositories ("repo@ref" -> sha).
  const sourceRefs = new Map<string, string>();
  let treeFailures = 0;
  let suspended = false;
  let installAccount = organization;
  let branchSha = sha;
  let tagSha: string | null = null;
  const app = new GitHubApp("42", pem, { [organization]: 100 }, locate, async (input, init) => {
    const url = new URL(String(input));
    assertEquals(url.origin, "https://api.github.com");
    const path = url.pathname;
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const authorization = new Headers(init?.headers).get("authorization") ?? "";
    calls.push({ method, path, body, authorization });
    if (path === "/app/installations/100") {
      return Response.json({
        account: { login: installAccount },
        app_id: 42,
        suspended_at: suspended ? "2026-01-01" : null,
      });
    }
    if (path === "/app/installations/100/access_tokens") {
      return Response.json({
        token: "installation-secret",
        expires_at: new Date(Date.now() + 3600000).toISOString(),
      });
    }
    assertEquals(authorization, "Bearer installation-secret");
    if (path === "/orgs/" + organization + "/repos" && method === "POST") {
      const repo = {
        id: 1000,
        full_name: organization + "/" + body.name,
        private: body.private,
        fork: false,
        default_branch: "main",
      };
      repos.set(repo.full_name, repo);
      return Response.json(repo, { status: 201 });
    }
    const full = path.split("/").slice(2, 4).join("/");
    if (path.endsWith("/actions/permissions")) {
      if (method === "PUT") {
        permissions.set(full, body.enabled);
        return new Response(null, { status: 204 });
      }
      return Response.json({ enabled: permissions.get(full) ?? true });
    }
    if (path.endsWith("/commits/main")) return Response.json({ sha: branchSha });
    if (path.includes("/commits/observer-runtime-")) {
      return tagSha ? Response.json({ sha: tagSha }) : Response.json({ message: "not found" }, { status: 404 });
    }
    if (path.includes("/commits/")) {
      const named = sourceRefs.get(full + "@" + decodeURIComponent(path.split("/commits/")[1]));
      return named ? Response.json({ sha: named }) : Response.json({ message: "No commit found" }, { status: 422 });
    }
    if (path.endsWith("/forks") && method === "POST") {
      const original = repos.get(full);
      const repo = {
        id: 1001,
        full_name: body.organization + "/" + body.name,
        private: false,
        fork: true,
        parent: { id: original.id },
        default_branch: "main",
      };
      repos.set(repo.full_name, repo);
      return Response.json(repo, { status: 202 });
    }
    if (path.includes("/zipball/")) {
      return new Response(null, {
        status: 302,
        headers: { location: "https://codeload.github.com/" + full + "/legacy.zip/" + sha },
      });
    }
    if (path.endsWith("/dispatches")) {
      return body.return_run_details ? Response.json({ workflow_run_id: 31337 }) : new Response(null, { status: 204 });
    }
    // Git Data API for backend-committed public-pool results.
    if (path.endsWith("/git/blobs")) return Response.json({ sha: "blob-" + body.content }, { status: 201 });
    if (path.endsWith("/git/trees")) {
      if (treeFailures > 0) {
        treeFailures--;
        return Response.json({ message: "Server Error" }, { status: 500 });
      }
      return Response.json({ sha: "tree-" + JSON.stringify(body.tree) }, { status: 201 });
    }
    if (path.endsWith("/git/commits")) return Response.json({ sha: "c".repeat(40) }, { status: 201 });
    if (path.endsWith("/git/refs") && method === "POST") {
      refs.set(body.ref, body.sha);
      return Response.json({}, { status: 201 });
    }
    if (path.includes("/git/ref/heads/")) {
      const ref = "refs/heads/" + path.split("/git/ref/heads/")[1];
      return refs.has(ref) ? Response.json({ object: { sha: refs.get(ref) } }) : Response.json({}, { status: 404 });
    }
    return repos.has(full) ? Response.json(repos.get(full)) : Response.json({ message: "not found" }, { status: 404 });
  });
  return {
    app,
    calls,
    repos,
    permissions,
    suspend: () => suspended = true,
    wrongAccount: () => installAccount = "outsider",
    wrongBranch: () => branchSha = "b".repeat(40),
    tag: (value: string) => tagSha = value,
    refs,
    sourceRefs,
    failTrees: (n: number) => treeFailures = n,
  };
}

Deno.test("placement uses the recorded organization and accepts only runner organizations 1-99", async () => {
  assertEquals(await placement(user, locate), assigned);
  assertEquals(assigned.organization, "AGENTIC-OBSERVER26-runner-12");
  assertEquals(assigned.privateRepository, "participant-" + user.replaceAll("-", ""));
  await assertRejects(() => placement("../../secret", locate), GitHubError);
  const asked: string[] = [];
  await placement(user.toUpperCase(), (id) => {
    asked.push(id);
    return Promise.resolve("AGENTIC-OBSERVER26-runner-1");
  });
  assertEquals(asked, [user]);
  for (const answer of ["AGENTIC-OBSERVER26-runner-100", "AGENTIC-OBSERVER26-runner-0", "outsider", null, 7]) {
    await assertRejects(() => placement(user, () => Promise.resolve(answer)), GitHubError, "invalid_placement");
  }
  await assertRejects(
    () => placement(user, () => Promise.reject(new Error("database detail"))),
    GitHubError,
    "placement_unavailable",
  );
  for (const i of [1, 7, 12, 13, 36, 99]) assert(isRunnerOrganization("AGENTIC-OBSERVER26-runner-" + i));
  for (
    const name of [
      "AGENTIC-OBSERVER26-runner-100",
      "AGENTIC-OBSERVER26-runner-0",
      "AGENTIC-OBSERVER26-runner-01",
      "AGENTIC-OBSERVER26-runner-1x",
    ]
  ) {
    assert(!isRunnerOrganization(name));
  }
});

Deno.test("only canonical GitHub repository links enter the ingestion queue", () => {
  assertEquals(sourceRepository("https://github.com/example/project.git/"), "example/project");
  for (
    const url of [
      "http://github.com/a/b",
      "https://github.com.evil.test/a/b",
      "https://x:y@github.com/a/b",
      "https://github.com/a/b/blob/main/x",
      "https://github.com/a/b/tree/",
      "https://github.com/a/b/commit/main",
      "https://github.com/a/b?download=1",
      "https://github.com/a/%2e%2e",
    ]
  ) {
    assertThrows(() => sourceRepository(url), GitHubError);
  }
});

Deno.test("installation identity is verified before using its token", async () => {
  for (const change of ["wrongAccount", "suspend"] as const) {
    const f = backend();
    f[change]();
    await assertRejects(() => f.app.privateParticipantRepository(user), GitHubError);
    assert(!f.calls.some((c) => c.path.endsWith("/access_tokens")));
  }
});

Deno.test("private repository provisioning is idempotent and disables source workflows", async () => {
  const f = backend();
  const first = await f.app.privateParticipantRepository(user);
  const again = await f.app.privateParticipantRepository(user);
  assertEquals(first, again);
  assertEquals(first.private, true);
  assertEquals(f.permissions.get(first.full_name), false);
  assertEquals(f.calls.filter((c) => c.path.endsWith("/repos") && c.method === "POST").length, 1);
  assertEquals(f.calls.filter((c) => c.path.endsWith("/access_tokens")).length, 1);
});

Deno.test("a preexisting public or forked repository cannot hold private evidence", async () => {
  for (const fields of [{ private: false, fork: false }, { private: true, fork: true }]) {
    const f = backend();
    f.repos.set(organization + "/" + assigned.privateRepository, {
      id: 1,
      full_name: organization + "/" + assigned.privateRepository,
      ...fields,
    });
    await assertRejects(() => f.app.privateParticipantRepository(user), GitHubError);
  }
});

Deno.test("forking fixes source commit before fork and keeps participant workflows disabled", async () => {
  const f = backend();
  f.repos.set("example/project", {
    id: 72,
    full_name: "example/project",
    private: false,
    fork: false,
    default_branch: "main",
  });
  const result = await f.app.forkPublicSource(user, "https://github.com/example/project");
  assertEquals(result.sourceCommit, sha);
  assertEquals(result.sourceRepository, "example/project");
  assertEquals(result.repository.private, false);
  assertEquals(f.permissions.get(result.repository.full_name), false);
  const read = f.calls.findIndex((c) => c.path === "/repos/example/project/commits/main");
  const fork = f.calls.findIndex((c) => c.path === "/repos/example/project/forks");
  assert(read >= 0 && fork > read);
});

Deno.test("private links require an authorized ZIP instead of exposing a source fork", async () => {
  const f = backend();
  f.repos.set("example/project", { id: 72, private: true, default_branch: "main" });
  await assertRejects(
    () => f.app.forkPublicSource(user, "https://github.com/example/project"),
    GitHubError,
    "private_source_requires_zip",
  );
  assert(!f.calls.some((c) => c.path.endsWith("/forks")));
});

Deno.test("only approved private control workflow commits may be dispatched", async () => {
  const f = backend();
  f.repos.set(organization + "/" + CONTROL_REPOSITORY, { id: 10, private: true, fork: false });
  await f.app.dispatch(organization, "observer-execute.yml", user, "x".repeat(43), sha);
  assertEquals(f.calls.at(-1)?.body, { ref: "main", inputs: { job_id: user, job_nonce: "x".repeat(43) } });
  // Only a fallback dispatch names the runner pool; older runtimes reject unknown inputs.
  await f.app.dispatch(organization, "observer-execute.yml", user, "x".repeat(43), sha, "self-hosted");
  assertEquals(f.calls.at(-1)?.body, {
    ref: "main",
    inputs: { job_id: user, job_nonce: "x".repeat(43), runner: "self-hosted" },
  });
  f.wrongBranch();
  await assertRejects(
    () => f.app.dispatch(organization, "observer-engine.yml", user, "x".repeat(43), sha),
    GitHubError,
  );
  assertEquals(f.calls.filter((c) => c.path.endsWith("/dispatches")).length, 2);
});

Deno.test("archive redirects return only codeload destinations without forwarding installation secrets", async () => {
  const f = backend();
  const url = await f.app.archiveDownload(organization, assigned.privateRepository, sha);
  assert(url.startsWith("https://codeload.github.com/"));
  assert(!url.includes("installation-secret"));
  assertEquals(f.calls.filter((c) => c.path.includes("/zipball/")).length, 1);
});

Deno.test("queued runtime releases survive a main update but a moved tag is rejected", async () => {
  const f = backend();
  f.repos.set(organization + "/" + CONTROL_REPOSITORY, { id: 10, private: true, fork: false });
  f.tag(sha);
  f.wrongBranch();
  await f.app.dispatch(organization, "observer-engine.yml", user, "x".repeat(43), sha);
  assertEquals(f.calls.at(-1)?.body.ref, "observer-runtime-" + sha);
  f.tag("b".repeat(40));
  await assertRejects(
    () => f.app.dispatch(organization, "observer-engine.yml", user, "x".repeat(43), sha),
    GitHubError,
  );
});

const expected: WorkflowIdentity = {
  organization,
  repositoryId: "123",
  organizationId: "456",
  workflow: "observer-engine.yml",
  approvedSha: sha,
  runId: "789",
  runAttempt: "1",
};
const claims = {
  repository_id: "123",
  repository_owner_id: "456",
  repository: organization + "/" + CONTROL_REPOSITORY,
  repository_visibility: "private",
  workflow_ref: organization + "/" + CONTROL_REPOSITORY + "/.github/workflows/observer-engine.yml@refs/heads/main",
  workflow_sha: sha,
  sha,
  ref: "refs/heads/main",
  event_name: "workflow_dispatch",
  run_id: "789",
  run_attempt: "1",
};
async function signed(overrides: Record<string, unknown> = {}, audience = "agentic-observer26") {
  return await new SignJWT({ ...claims, ...overrides }).setProtectedHeader({ alg: "RS256", kid: "test-key" })
    .setIssuedAt().setExpirationTime("5m").setIssuer("https://token.actions.githubusercontent.com")
    .setAudience(audience).setJti(crypto.randomUUID()).setSubject(
      "repo:" + organization + "/" + CONTROL_REPOSITORY + ":ref:refs/heads/main",
    )
    .sign(pair.privateKey);
}

Deno.test("OIDC validates signature, audience and exact trusted workflow identity", async () => {
  const verified = await verifyWorkflowIdentity(await signed(), expected, keys);
  assertEquals(verified.runId, "789");
  await assertRejects(
    async () => verifyWorkflowIdentity(await signed({}, "different-service"), expected, keys),
    GitHubError,
  );
});

Deno.test("an unreachable GitHub key set is retryable (503), a bad token stays 401", async () => {
  const token = await signed();
  for (const failure of [new errors.JWKSTimeout(), new errors.JOSEError("Expected 200 OK"), new TypeError("network")]) {
    const error = await assertRejects(
      () => verifyWorkflowIdentity(token, expected, () => Promise.reject(failure)),
      GitHubError,
    );
    assertEquals([error.code, error.status], ["workflow_identity_unavailable", 503]);
  }
  for (const bad of [token.slice(0, -4) + "AAAA", "not.a.jwt"]) {
    const error = await assertRejects(() => verifyWorkflowIdentity(bad, expected, keys), GitHubError);
    assertEquals([error.code, error.status], ["invalid_workflow_identity", 401]);
  }
});

Deno.test("release OIDC requires the exact approved tag and workflow commit", async () => {
  const ref = "refs/tags/observer-runtime-" + sha;
  const workflow_ref = organization + "/" + CONTROL_REPOSITORY + "/.github/workflows/observer-engine.yml@" + ref;
  assertEquals((await verifyWorkflowIdentity(await signed({ ref, workflow_ref }), expected, keys)).runId, "789");
  await assertRejects(
    async () => verifyWorkflowIdentity(await signed({ ref: ref + "x", workflow_ref }), expected, keys),
    GitHubError,
  );
  await assertRejects(
    async () => verifyWorkflowIdentity(await signed({ ref, workflow_ref, sha: "b".repeat(40) }), expected, keys),
    GitHubError,
  );
});

Deno.test("a valid GitHub signature from another repo, workflow, ref or attempt cannot claim a job", async () => {
  for (
    const invalid of [
      { repository_id: "999" },
      { repository_owner_id: "999" },
      { repository_visibility: "public" },
      { workflow_sha: "b".repeat(40) },
      { sha: "b".repeat(40) },
      { ref: "refs/pull/1/merge" },
      { event_name: "pull_request_target" },
      { workflow_ref: organization + "/" + CONTROL_REPOSITORY + "/.github/workflows/participant.yml@refs/heads/main" },
      { run_id: "other" },
      { run_attempt: "2" },
    ]
  ) {
    await assertRejects(async () => verifyWorkflowIdentity(await signed(invalid), expected, keys), GitHubError);
  }
});

Deno.test("the public pool dispatches only its approved tag with an opaque job id and returns the run id", async () => {
  const f = backend();
  f.repos.set(organization + "/" + PUBLIC_POOL_REPOSITORY, { id: 77, private: false, fork: false });
  await assertRejects(() => f.app.dispatchPublic(organization, "77", user, sha), GitHubError); // no tag
  f.tag(sha);
  assertEquals(await f.app.dispatchPublic(organization, "77", user, sha), "31337");
  assertEquals(
    f.calls.at(-1)?.path,
    "/repos/" + organization + "/observer-public/actions/workflows/observer-engine.yml/dispatches",
  );
  assertEquals(f.calls.at(-1)?.body, {
    ref: "observer-runtime-" + sha,
    inputs: { job_id: user },
    return_run_details: true,
  });
  await assertRejects(
    () => f.app.dispatchPublic(organization, "78", user, sha),
    GitHubError,
    "public_pool_repository_mismatch",
  );
  f.repos.set(organization + "/" + PUBLIC_POOL_REPOSITORY, { id: 77, private: true, fork: false });
  await assertRejects(
    () => f.app.dispatchPublic(organization, "77", user, sha),
    GitHubError,
    "public_pool_repository_mismatch",
  );
});

Deno.test("a backend-committed result is one fixed root commit on the run's result branch", async () => {
  const f = backend();
  const run = "00000000-0000-4000-8000-0000000000bb";
  const files = [{ path: "decisions.csv", data: new TextEncoder().encode("a"), executable: false }];
  const path = await f.app.commitResult(user, run, files);
  assertEquals(path, "github:" + organization + "/" + assigned.privateRepository + "@" + "c".repeat(40));
  const commit = f.calls.find((c) => c.path.endsWith("/git/commits"))!;
  assertEquals(commit.body.parents, []);
  assertEquals(commit.body.author.date, "2026-01-01T00:00:00Z");
  assert(commit.body.message.startsWith("Evaluation result " + run + "\n\nSHA256 "));
  assertEquals(f.refs.get("refs/heads/results/" + run), "c".repeat(40));
  // Idempotent for the same content; a different existing result is a conflict.
  assertEquals(await f.app.commitResult(user, run, files), path);
  f.refs.set("refs/heads/results/" + run, "d".repeat(40));
  await assertRejects(() => f.app.commitResult(user, run, files), GitHubError, "result_conflict");
});

Deno.test("public-pool OIDC needs the public repository, public visibility and the approved tag", async () => {
  const ref = "refs/tags/observer-runtime-" + sha;
  const repository = organization + "/" + PUBLIC_POOL_REPOSITORY;
  const pub: WorkflowIdentity = { ...expected, repository: PUBLIC_POOL_REPOSITORY, visibility: "public" };
  const ok = {
    repository,
    repository_visibility: "public",
    ref,
    workflow_ref: repository + "/.github/workflows/observer-engine.yml@" + ref,
  };
  assertEquals((await verifyWorkflowIdentity(await signed(ok), pub, keys)).runId, "789");
  for (
    const invalid of [
      { repository_visibility: "private" },
      { ref: "refs/heads/main", workflow_ref: repository + "/.github/workflows/observer-engine.yml@refs/heads/main" },
      { repository: organization + "/" + CONTROL_REPOSITORY },
    ]
  ) {
    await assertRejects(
      async () => verifyWorkflowIdentity(await signed({ ...ok, ...invalid }), pub, keys),
      GitHubError,
    );
  }
  // The private pool never accepts a public repository, and names are fixed.
  await assertRejects(async () => verifyWorkflowIdentity(await signed(ok), expected, keys), GitHubError);
  for (const config of [{ ...pub, visibility: "private" as const }, { ...pub, repository: "other" }]) {
    await assertRejects(
      async () => verifyWorkflowIdentity(await signed(ok), config, keys),
      GitHubError,
      "invalid_workflow_configuration",
    );
  }
});

Deno.test("a transient GitHub error while committing a public-pool result is retried", async () => {
  const f = backend();
  const run = "00000000-0000-4000-8000-0000000000cc";
  const files = [{ path: "decisions.csv", data: new TextEncoder().encode("a"), executable: false }];
  f.failTrees(2);
  assertEquals(
    await f.app.commitResult(user, run, files),
    "github:" + organization + "/" + assigned.privateRepository + "@" + "c".repeat(40),
  );
  assertEquals(f.calls.filter((c) => c.path.endsWith("/git/trees")).length, 3);
  f.failTrees(10);
  await assertRejects(() => f.app.commitResult(user, "00000000-0000-4000-8000-0000000000cd", files), GitHubError);
});

Deno.test("a submission resolves the exact commit and its zipball without forking", async () => {
  const f = backend();
  f.repos.set("example/project", {
    id: 72,
    full_name: "example/project",
    private: false,
    fork: false,
    default_branch: "main",
  });
  const result = await f.app.resolvePublicSource(user, "https://github.com/example/project");
  assertEquals(result.commit, sha);
  assertEquals(result.sourceRepository, "example/project");
  assert(result.archiveUrl.startsWith("https://codeload.github.com/example/project/"));
  assert(result.archiveUrl.endsWith(sha));
  assert(!f.calls.some((c) => c.path.endsWith("/forks")));
  f.repos.set("example/secret", { id: 73, private: true, default_branch: "main" });
  await assertRejects(
    () => f.app.resolvePublicSource(user, "https://github.com/example/secret"),
    GitHubError,
    "private_source_requires_zip",
  );
});

Deno.test("a fork for a pinned submission keeps the submitted commit", async () => {
  const f = backend();
  f.repos.set("example/project", {
    id: 72,
    full_name: "example/project",
    private: false,
    fork: false,
    default_branch: "main",
  });
  const pinned = "e".repeat(40);
  const result = await f.app.forkPublicSource(user, "https://github.com/example/project", pinned);
  assertEquals(result.sourceCommit, pinned);
  assert(!f.calls.some((c) => c.path === "/repos/example/project/commits/main"));
});

Deno.test("branch, tag, commit and folder links are recognized; the ref is resolved later", () => {
  assertEquals(parseSourceUrl("https://github.com/a/b"), { repository: "a/b", tree: null, commit: null });
  assertEquals(parseSourceUrl("https://www.github.com/a/b.git/"), { repository: "a/b", tree: null, commit: null });
  assertEquals(parseSourceUrl("https://github.com/a/b/tree/feature/x/apps/agent/"), {
    repository: "a/b",
    tree: ["feature", "x", "apps", "agent"],
    commit: null,
  });
  assertEquals(parseSourceUrl("https://github.com/a/b/tree/v1.0%2Brc"), {
    repository: "a/b",
    tree: ["v1.0+rc"],
    commit: null,
  });
  assertEquals(parseSourceUrl("https://github.com/a/b/commit/" + "A".repeat(40)), {
    repository: "a/b",
    tree: null,
    commit: "a".repeat(40),
  });
  assertEquals(sourceRef(" feature/x "), "feature/x");
  assertEquals(sourceRef(""), null);
  for (const bad of ["a b", "../x", "x..y", "/x", "x/", "x.lock", "-x", "a/.b", 3]) {
    assertThrows(() => sourceRef(bad), GitHubError, "invalid_source_ref");
  }
  assertEquals(sourceSubdir("./apps/agent/"), "apps/agent");
  assertEquals(sourceSubdir("  "), null);
  for (const bad of ["a/../b", "a//b", "a\\b", ".git/x", "a/./b"]) {
    assertThrows(() => sourceSubdir(bad), GitHubError, "invalid_source_subdir");
  }
});

Deno.test("a submission resolves the named branch, tag or commit and the folder", async () => {
  const f = backend();
  f.repos.set("example/project", {
    id: 72,
    full_name: "example/project",
    private: false,
    fork: false,
    default_branch: "main",
  });
  const branch = "b".repeat(40), tag = "c".repeat(40), pinned = "d".repeat(40);
  f.sourceRefs.set("example/project@feature/x", branch);
  f.sourceRefs.set("example/project@v2", tag);
  f.sourceRefs.set("example/project@" + pinned, pinned);
  const resolve = (url: string, options = {}) => f.app.resolvePublicSource(user, url, options);
  // Default branch: unchanged behaviour, no ref recorded.
  let r = await resolve("https://github.com/example/project");
  assertEquals([r.commit, r.ref, r.subdir], [sha, null, null]);
  // A branch with "/" followed by a folder: the shortest existing ref wins.
  r = await resolve("https://github.com/example/project/tree/feature/x/apps/agent");
  assertEquals([r.commit, r.ref, r.subdir], [branch, "feature/x", "apps/agent"]);
  // The resolved commit's zipball is returned, not the branch's.
  assert(f.calls.some((c) => c.path === "/repos/example/project/zipball/" + branch));
  r = await resolve("https://github.com/example/project/tree/v2");
  assertEquals([r.commit, r.ref, r.subdir], [tag, "v2", null]);
  r = await resolve("https://github.com/example/project/commit/" + pinned, { subdir: "src" });
  assertEquals([r.commit, r.ref, r.subdir], [pinned, pinned, "src"]);
  // Fields instead of a long link.
  r = await resolve("https://github.com/example/project", { ref: "feature/x", subdir: "apps/agent" });
  assertEquals([r.commit, r.ref, r.subdir], [branch, "feature/x", "apps/agent"]);
  r = await resolve("https://github.com/example/project/tree/feature/x/apps/agent", { ref: "feature/x" });
  assertEquals([r.ref, r.subdir], ["feature/x", "apps/agent"]);
  r = await resolve("https://github.com/example/project", { subdir: "apps" });
  assertEquals([r.commit, r.ref, r.subdir], [sha, null, "apps"]);
  await assertRejects(
    () => resolve("https://github.com/example/project/tree/nope/apps"),
    GitHubError,
    "source_ref_not_found",
  );
  await assertRejects(
    () => resolve("https://github.com/example/project", { ref: "nope" }),
    GitHubError,
    "source_ref_not_found",
  );
  await assertRejects(
    () => resolve("https://github.com/example/project/tree/v2", { ref: "feature/x" }),
    GitHubError,
    "source_options_conflict",
  );
  await assertRejects(
    () => resolve("https://github.com/example/project/tree/v2/apps", { subdir: "other" }),
    GitHubError,
    "source_options_conflict",
  );
  await assertRejects(
    () => resolve("https://github.com/example/project/commit/" + pinned, { ref: "v2" }),
    GitHubError,
    "source_options_conflict",
  );
});
