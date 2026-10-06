/** GitHub control-plane operations. Installation tokens never go to projects. */
import { createPrivateKey } from "node:crypto";
import { createRemoteJWKSet, errors, importPKCS8, jwtVerify, SignJWT } from "npm:jose@6.1.0";
import type { JWTVerifyGetKey } from "npm:jose@6.1.0";
import { toBase64 } from "./observer-seal.ts";

/** Runner organization names; which of them are usable comes from the installation table. */
export const RUNNER_ORGANIZATION = /^AGENTIC-OBSERVER26-runner-([1-9]|[1-9][0-9])$/;
export const RUNNER_ORGANIZATION_PATTERN = "AGENTIC-OBSERVER26-runner-(?:[1-9]|[1-9][0-9])";
export function isRunnerOrganization(value: unknown): value is string {
  return typeof value === "string" && RUNNER_ORGANIZATION.test(value);
}
export const CONTROL_REPOSITORY = "observer-control";
/**
 * The public-repository runner pool (ops/public-runner-pool.md): a public
 * repository in one runner organization with the trusted engine workflow only.
 * Its dispatch input is an opaque job id and all private data is sealed.
 */
export const PUBLIC_POOL_REPOSITORY = "observer-public";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA = /^[0-9a-f]{40}$/;
const NAME = /^[A-Za-z0-9_.-]{1,100}$/;
const API = "https://api.github.com";

export class GitHubError extends Error {
  constructor(public code: string, public status = 0) {
    super(code);
  }
}

/** Returns the participant's recorded runner organization (public.observer_placement). */
export type Locator = (userId: string) => Promise<unknown>;

export function databaseLocator(rpc: (name: string, args: Record<string, unknown>) => Promise<unknown>): Locator {
  return (userId) => rpc("observer_placement", { p_user: userId });
}

/**
 * A participant's organization is recorded once and stays stable even when
 * runner organizations are added; only a dispatch failover may move it,
 * together with the pending job, so claim-time credentials stay in one
 * organization.
 */
export async function placement(userId: string, locate: Locator) {
  if (!UUID.test(userId)) throw new GitHubError("invalid_participant");
  let organization: unknown;
  try {
    organization = await locate(userId.toLowerCase());
  } catch {
    throw new GitHubError("placement_unavailable");
  }
  if (!isRunnerOrganization(organization)) throw new GitHubError("invalid_placement");
  return {
    organization,
    privateRepository: "participant-" + userId.toLowerCase().replaceAll("-", ""),
  };
}

/**
 * A public GitHub source as typed by a participant: the repository page, a branch,
 * tag or commit page (/tree/<ref>[/<folder>]) or a commit page (/commit/<sha>).
 * `tree` holds the decoded path after /tree/; which part of it is the ref is only
 * known once GitHub resolves it (branch names may contain "/").
 */
export type SourceUrl = { repository: string; tree: string[] | null; commit: string | null };

export function parseSourceUrl(input: string): SourceUrl {
  let url: URL;
  try {
    url = new URL(input.trim());
  } catch {
    throw new GitHubError("invalid_repository_url");
  }
  if (
    url.protocol !== "https:" || !["github.com", "www.github.com"].includes(url.hostname) || url.username ||
    url.password || url.port || url.search || url.hash
  ) throw new GitHubError("invalid_repository_url");
  let parts: string[];
  try {
    parts = url.pathname.replace(/\/+$/, "").slice(1).split("/").map(decodeURIComponent);
  } catch {
    throw new GitHubError("invalid_repository_url");
  }
  if (parts.length < 2 || parts.slice(0, 2).some((p) => !NAME.test(p) || p === "." || p === "..")) {
    throw new GitHubError("invalid_repository_url");
  }
  const repo = parts[1].replace(/\.git$/, "");
  if (!repo) throw new GitHubError("invalid_repository_url");
  const repository = parts[0] + "/" + repo, rest = parts.slice(2);
  if (!rest.length) return { repository, tree: null, commit: null };
  if (rest[0] === "tree" && rest.length > 1 && rest.slice(1).every((p) => p && !p.includes("\0"))) {
    return { repository, tree: rest.slice(1), commit: null };
  }
  if (rest[0] === "commit" && rest.length === 2 && SHA.test(rest[1].toLowerCase())) {
    return { repository, tree: null, commit: rest[1].toLowerCase() };
  }
  throw new GitHubError("invalid_repository_url");
}

export function sourceRepository(input: string): string {
  return parseSourceUrl(input).repository;
}

/** An optional branch, tag or commit name (Git ref rules, conservatively). */
export function sourceRef(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") throw new GitHubError("invalid_source_ref");
  const ref = value.trim();
  if (!ref) return null;
  if (
    ref.length > 200 || !/^[A-Za-z0-9._\/+@-]+$/.test(ref) || ref.includes("..") || ref.includes("//") ||
    ref.includes("@{") || ref.startsWith("/") || ref.endsWith("/") || ref.endsWith(".") || ref.endsWith(".lock") ||
    ref.startsWith("-") || ref.split("/").some((p) => p.startsWith("."))
  ) throw new GitHubError("invalid_source_ref");
  return ref;
}

/** An optional project folder inside the repository, as a normalized relative path. */
export function sourceSubdir(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") throw new GitHubError("invalid_source_subdir");
  const path = value.trim().replace(/^\.\//, "").replace(/^\/+|\/+$/g, "");
  if (!path) return null;
  const parts = path.split("/");
  if (
    path.length > 300 || path.includes("\\") || path.includes("\0") ||
    parts.some((p) => !p || p === "." || p === ".." || p.toLowerCase() === ".git")
  ) throw new GitHubError("invalid_source_subdir");
  return parts.join("/");
}

export type ResolvedSource = {
  sourceRepository: string;
  commit: string;
  archiveUrl: string;
  /** The branch, tag or commit named by the participant; null means the default branch. */
  ref: string | null;
  subdir: string | null;
};

function target(organization: string, repository: string) {
  if (
    !isRunnerOrganization(organization) || !NAME.test(repository) ||
    repository === "." || repository === ".."
  ) throw new GitHubError("unapproved_repository");
  return organization + "/" + repository;
}

type Repository = {
  id: number;
  full_name: string;
  private: boolean;
  fork: boolean;
  default_branch: string;
  parent?: { id: number };
};
type Token = { token: string; expires_at: string };
export type GitHubTransport = typeof fetch;

/** project_platform.package.project_digest: length-prefixed path, mode and bytes per file. */
export async function projectDigest(files: { path: string; data: Uint8Array; executable: boolean }[]) {
  const sorted = [...files].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  const parts: Uint8Array[] = [];
  for (const file of sorted) {
    for (
      const field of [
        new TextEncoder().encode(file.path),
        new TextEncoder().encode(file.executable ? "x" : "-"),
        file.data,
      ]
    ) {
      const length = new Uint8Array(8);
      new DataView(length.buffer).setBigUint64(0, BigInt(field.length));
      parts.push(length, field);
    }
  }
  const all = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let offset = 0;
  for (const part of parts) {
    all.set(part, offset);
    offset += part.length;
  }
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", all)), (b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export class GitHubApp {
  private tokens = new Map<string, Token>();
  constructor(
    private appId: string,
    private privateKey: string,
    private installations: Record<string, number>,
    private locate: Locator,
    private fetcher: GitHubTransport = fetch,
  ) {
    if (
      !/^\d+$/.test(appId) || Object.keys(installations).some((org) => !isRunnerOrganization(org)) ||
      Object.values(installations).some((id) => !Number.isSafeInteger(id) || id < 1)
    ) {
      throw new GitHubError("invalid_app_configuration");
    }
  }

  private async appToken(): Promise<string> {
    const pem = createPrivateKey(this.privateKey).export({ type: "pkcs8", format: "pem" }).toString();
    const key = await importPKCS8(pem, "RS256");
    const now = Math.floor(Date.now() / 1000);
    return await new SignJWT({}).setProtectedHeader({ alg: "RS256" })
      .setIssuer(this.appId).setIssuedAt(now - 30).setExpirationTime(now + 540).sign(key);
  }

  private async request(path: string, token: string, method = "GET", body?: unknown): Promise<any> {
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("..")) {
      throw new GitHubError("invalid_github_path");
    }
    let response: Response;
    try {
      response = await this.fetcher(API + path, {
        method,
        redirect: "error",
        signal: AbortSignal.timeout(30000),
        headers: {
          accept: "application/vnd.github+json",
          authorization: "Bearer " + token,
          "content-type": "application/json",
          "x-github-api-version": "2022-11-28",
          "user-agent": "Agentic-Observer26",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new GitHubError("github_unavailable");
    }
    if (!response.ok) {
      await response.body?.cancel();
      // Error bodies may echo supplied secrets or private repository details.
      throw new GitHubError(response.status === 404 ? "github_not_found" : "github_request_failed", response.status);
    }
    if (response.status === 204) return null;
    return await response.json();
  }

  async installationToken(organization: string): Promise<string> {
    const installation = this.installations[organization];
    if (!isRunnerOrganization(organization) || !installation) {
      throw new GitHubError("organization_not_installed");
    }
    const cached = this.tokens.get(organization);
    if (cached && Date.parse(cached.expires_at) > Date.now() + 120000) return cached.token;
    const identity = await this.request("/app/installations/" + installation, await this.appToken());
    if (
      identity.account?.login?.toLowerCase() !== organization.toLowerCase() ||
      identity.app_id !== Number(this.appId) || identity.suspended_at
    ) throw new GitHubError("installation_identity_mismatch");
    const value = await this.request(
      "/app/installations/" + installation + "/access_tokens",
      await this.appToken(),
      "POST",
      {},
    );
    if (typeof value.token !== "string" || !Number.isFinite(Date.parse(value.expires_at))) {
      throw new GitHubError("invalid_installation_token");
    }
    this.tokens.set(organization, value);
    return value.token;
  }

  async repository(organization: string, name: string): Promise<Repository> {
    const full = target(organization, name);
    return await this.request("/repos/" + full, await this.installationToken(organization));
  }

  async disableSourceWorkflows(organization: string, name: string) {
    const full = target(organization, name);
    if (name === CONTROL_REPOSITORY) throw new GitHubError("control_repository_is_not_a_source");
    const token = await this.installationToken(organization);
    await this.request("/repos/" + full + "/actions/permissions", token, "PUT", { enabled: false });
    const permissions = await this.request("/repos/" + full + "/actions/permissions", token);
    if (permissions.enabled !== false) throw new GitHubError("source_workflows_still_enabled");
  }

  async privateParticipantRepository(userId: string): Promise<Repository> {
    const { organization, privateRepository } = await placement(userId, this.locate);
    const token = await this.installationToken(organization);
    let repo: Repository;
    try {
      repo = await this.repository(organization, privateRepository);
    } catch (error) {
      if (!(error instanceof GitHubError) || error.status !== 404) throw error;
      try {
        repo = await this.request("/orgs/" + organization + "/repos", token, "POST", {
          name: privateRepository,
          private: true,
          auto_init: true,
          description: "Private competition source revisions and evaluation evidence.",
          has_issues: false,
          has_projects: false,
          has_wiki: false,
        });
      } catch (creation) {
        // Concurrent or ambiguous creation: verify the deterministic target.
        if (!(creation instanceof GitHubError) || (creation.status !== 422 && creation.status !== 0)) throw creation;
        repo = await this.repository(organization, privateRepository);
      }
    }
    if (
      !repo.private || repo.fork ||
      repo.full_name.toLowerCase() !== (organization + "/" + privateRepository).toLowerCase()
    ) {
      throw new GitHubError("private_repository_identity_mismatch");
    }
    await this.disableSourceWorkflows(organization, privateRepository);
    return repo;
  }

  async snapshotWriteToken(userId: string, includeWorkflows = true): Promise<string> {
    const { organization } = await placement(userId, this.locate);
    const repo = await this.privateParticipantRepository(userId);
    // A preparation job can write exactly this participant's repository. It does
    // not receive the organization-wide installation token or administration access.
    const value = await this.request(
      "/app/installations/" + this.installations[organization] + "/access_tokens",
      await this.appToken(),
      "POST",
      {
        repository_ids: [repo.id],
        permissions: { contents: "write", ...(includeWorkflows ? { workflows: "write" } : {}) },
      },
    );
    if (typeof value.token !== "string") throw new GitHubError("invalid_installation_token");
    return value.token;
  }

  /**
   * The exact commit a public source points at now (the default branch, or the
   * branch, tag or commit the participant named in the URL or in `options`), the
   * project folder inside it, and a temporary codeload URL of that commit's zipball
   * (no fork, nothing written). Used at submission time so every submitted version
   * is pinned and preserved.
   */
  async resolvePublicSource(
    userId: string,
    url: string,
    options: { ref?: unknown; subdir?: unknown } = {},
  ): Promise<ResolvedSource> {
    const parsed = parseSourceUrl(url);
    const source = parsed.repository;
    const ref = sourceRef(options.ref), subdir = sourceSubdir(options.subdir);
    // Candidate (ref, folder) splits, shortest ref first. Git cannot hold both
    // "a" and "a/b" as branches, so at most one split of a /tree/ path resolves.
    let candidates: [string | null, string | null][], conflicting = false;
    if (parsed.commit) {
      if (ref && ref.toLowerCase() !== parsed.commit) throw new GitHubError("source_options_conflict");
      candidates = [[parsed.commit, subdir]];
    } else if (parsed.tree) {
      const tree = parsed.tree;
      const splits = ref
        ? (tree.slice(0, ref.split("/").length).join("/") === ref ? [ref.split("/").length] : [])
        : Array.from({ length: Math.min(tree.length, 10) }, (_, i) => i + 1);
      const all = splits.map((n): [string, string | null] => [
        tree.slice(0, n).join("/"),
        sourceSubdir(tree.slice(n).join("/")),
      ]);
      // A folder in the link must agree with the folder field.
      candidates = all.filter(([, folder]) => !folder || !subdir || folder === subdir)
        .map(([name, folder]) => [name, folder ?? subdir]);
      conflicting = candidates.length < all.length;
      if (!candidates.length) throw new GitHubError("source_options_conflict");
    } else candidates = [[ref, subdir]];
    const { organization } = await placement(userId, this.locate);
    const token = await this.installationToken(organization);
    const original: Repository = await this.request("/repos/" + source, token);
    if (original.private) throw new GitHubError("private_source_requires_zip");
    let commit: { sha?: unknown } | null = null, chosen: [string | null, string | null] = [null, subdir];
    for (const [name, folder] of candidates) {
      try {
        commit = await this.request(
          "/repos/" + source + "/commits/" + encodeURIComponent(name ?? original.default_branch),
          token,
        );
        chosen = [name, folder];
        break;
      } catch (error) {
        // 404/422: no such branch, tag or commit; try the next split.
        if (!(error instanceof GitHubError) || (error.status !== 404 && error.status !== 422)) throw error;
      }
    }
    if (!commit) throw new GitHubError(conflicting ? "source_options_conflict" : "source_ref_not_found");
    if (typeof commit.sha !== "string" || !SHA.test(commit.sha)) throw new GitHubError("invalid_source_commit");
    const response = await this.fetcher(API + "/repos/" + source + "/zipball/" + commit.sha, {
      redirect: "manual",
      signal: AbortSignal.timeout(30000),
      headers: {
        authorization: "Bearer " + token,
        accept: "application/vnd.github+json",
        "user-agent": "Agentic-Observer26",
      },
    });
    await response.body?.cancel();
    const location = response.headers.get("location");
    if (response.status !== 302 || !location) throw new GitHubError("archive_not_ready", response.status);
    const archive = new URL(location);
    if (
      archive.protocol !== "https:" || archive.hostname !== "codeload.github.com" || archive.username ||
      archive.password
    ) {
      throw new GitHubError("unexpected_archive_destination");
    }
    return {
      sourceRepository: source,
      commit: commit.sha,
      archiveUrl: archive.href,
      ref: chosen[0],
      subdir: chosen[1],
    };
  }

  async forkPublicSource(
    userId: string,
    url: string,
    // The commit pinned at submission (observer_revisions.submitted_commit), if any.
    pinned?: string | null,
  ): Promise<{ repository: Repository; sourceCommit: string; sourceRepository: string }> {
    const source = sourceRepository(url);
    const { organization } = await placement(userId, this.locate);
    const token = await this.installationToken(organization);
    const original: Repository = await this.request("/repos/" + source, token);
    if (original.private) throw new GitHubError("private_source_requires_zip");
    // Resolve before forking: a moving default branch cannot alter the submitted revision.
    // A revision pinned at submission keeps exactly that commit.
    const commit = pinned
      ? { sha: pinned }
      : await this.request("/repos/" + source + "/commits/" + encodeURIComponent(original.default_branch), token);
    if (!SHA.test(commit.sha)) throw new GitHubError("invalid_source_commit");
    const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(String(original.id))));
    const name = "source-" + Array.from(hash.slice(0, 10), (n) => n.toString(16).padStart(2, "0")).join("");
    let repo: Repository;
    try {
      repo = await this.repository(organization, name);
    } catch (error) {
      if (!(error instanceof GitHubError) || error.status !== 404) throw error;
      repo = await this.request("/repos/" + source + "/forks", token, "POST", {
        organization,
        name,
        default_branch_only: true,
      });
    }
    // Fork creation is asynchronous. The preparation queue retries the same target.
    // Do not copy workflows or enable Actions while waiting for the fork.
    if (!repo.fork || repo.private || repo.parent?.id !== original.id) {
      repo = await this.repository(organization, name);
      if (!repo.fork || repo.private || repo.parent?.id !== original.id) throw new GitHubError("fork_not_ready");
    }
    await this.disableSourceWorkflows(organization, name);
    return { repository: repo, sourceCommit: commit.sha, sourceRepository: source };
  }

  async dispatch(
    organization: string,
    workflow: "observer-prepare.yml" | "observer-execute.yml" | "observer-engine.yml" | "observer-score.yml",
    jobId: string,
    nonce: string,
    approvedSha: string,
    // Only the organizer's fallback runtime declares this input; older
    // runtimes reject unknown inputs, so it is sent only when needed.
    runner?: "self-hosted",
  ) {
    if (!UUID.test(jobId) || !/^[A-Za-z0-9_-]{40,100}$/.test(nonce) || !SHA.test(approvedSha)) {
      throw new GitHubError("invalid_job_dispatch");
    }
    const full = target(organization, CONTROL_REPOSITORY);
    const token = await this.installationToken(organization);
    const repo = await this.repository(organization, CONTROL_REPOSITORY);
    if (!repo.private || repo.fork) throw new GitHubError("control_repository_must_be_private");
    let ref = "observer-runtime-" + approvedSha;
    let branch;
    try {
      branch = await this.request("/repos/" + full + "/commits/" + ref, token);
    } catch (error) {
      // Transitional support for already approved main-only control runtimes.
      if (!(error instanceof GitHubError) || error.status !== 404) throw error;
      ref = "main";
      branch = await this.request("/repos/" + full + "/commits/main", token);
    }
    if (branch.sha !== approvedSha) throw new GitHubError("control_revision_not_approved");
    await this.request("/repos/" + full + "/actions/workflows/" + workflow + "/dispatches", token, "POST", {
      ref,
      inputs: runner ? { job_id: jobId, job_nonce: nonce, runner } : { job_id: jobId, job_nonce: nonce },
    });
  }

  /**
   * Dispatch an engine job to the public pool repository. Anyone can read a
   * public run, so the only input is the opaque job id; the returned run id is
   * what the job's claim is bound to instead of a nonce. Only the approved
   * runtime tag is dispatched, never a branch.
   */
  async dispatchPublic(
    organization: string,
    repositoryId: string,
    jobId: string,
    approvedSha: string,
    kind: "engine" | "score" = "engine",
  ) {
    if (
      !UUID.test(jobId) || !SHA.test(approvedSha) || !/^\d+$/.test(repositoryId) || !["engine", "score"].includes(kind)
    ) {
      throw new GitHubError("invalid_job_dispatch");
    }
    const full = target(organization, PUBLIC_POOL_REPOSITORY);
    const token = await this.installationToken(organization);
    const repo = await this.repository(organization, PUBLIC_POOL_REPOSITORY);
    if (repo.private || repo.fork || String(repo.id) !== repositoryId) {
      throw new GitHubError("public_pool_repository_mismatch");
    }
    const ref = "observer-runtime-" + approvedSha;
    const tag = await this.request("/repos/" + full + "/commits/" + ref, token);
    if (tag.sha !== approvedSha) throw new GitHubError("control_revision_not_approved");
    const run = await this.request(
      "/repos/" + full + "/actions/workflows/observer-" + kind + ".yml/dispatches",
      token,
      "POST",
      { ref, inputs: { job_id: jobId }, return_run_details: true },
    );
    const id = run?.workflow_run_id;
    if (!Number.isSafeInteger(id) || id < 1) throw new GitHubError("dispatch_run_unknown");
    return String(id);
  }

  /**
   * Commit an opened public-pool result to the team's private repository, as
   * the trusted job would (project_platform.repository.SnapshotRepository):
   * one root commit with fixed author and date on refs/heads/results/<run>,
   * never a force update. Uses a token limited to that one repository.
   */
  /**
   * GitHub API call for commitResult only (the public pool): transient GitHub
   * failures (5xx, network) are retried here, since a lost result cannot be
   * recomputed. Every step is idempotent: blobs, trees and commits are
   * content-addressed and the ref is checked before it is created.
   */
  private async resultRequest(path: string, token: string, method = "GET", body?: unknown) {
    for (let attempt = 0;; attempt++) {
      try {
        return await this.request(path, token, method, body);
      } catch (error) {
        const transient = error instanceof GitHubError &&
          (error.code === "github_unavailable" || error.status >= 500);
        if (!transient || attempt >= 3) throw error;
        await new Promise((resolve) => setTimeout(resolve, 1000 * 2 ** attempt));
      }
    }
  }

  async commitResult(userId: string, runId: string, files: { path: string; data: Uint8Array; executable: boolean }[]) {
    if (!UUID.test(runId) || !files.length) throw new GitHubError("invalid_result_snapshot");
    const run = runId.toLowerCase();
    const repo = await this.privateParticipantRepository(userId);
    const full = repo.full_name;
    const token = await this.snapshotWriteToken(userId, false);
    const tree = [];
    for (const file of files) {
      const blob = await this.resultRequest("/repos/" + full + "/git/blobs", token, "POST", {
        content: toBase64(file.data),
        encoding: "base64",
      });
      tree.push({ path: file.path, mode: file.executable ? "100755" : "100644", type: "blob", sha: blob.sha });
    }
    const created = await this.resultRequest("/repos/" + full + "/git/trees", token, "POST", { tree });
    const identity = { name: "Agentic Observer", email: "observer@create.gosim.org", date: "2026-01-01T00:00:00Z" };
    const commit = await this.resultRequest("/repos/" + full + "/git/commits", token, "POST", {
      message: "Evaluation result " + run + "\n\nSHA256 " + await projectDigest(files),
      tree: created.sha,
      parents: [],
      author: identity,
      committer: identity,
    });
    if (!SHA.test(commit?.sha)) throw new GitHubError("invalid_result_snapshot");
    const ref = "results/" + run;
    let existing;
    try {
      existing = await this.resultRequest("/repos/" + full + "/git/ref/heads/" + ref, token);
    } catch (error) {
      if (!(error instanceof GitHubError) || error.status !== 404) throw error;
      try {
        await this.resultRequest("/repos/" + full + "/git/refs", token, "POST", {
          ref: "refs/heads/" + ref,
          sha: commit.sha,
        });
      } catch (creation) {
        // A concurrent identical store may have created it first.
        if (!(creation instanceof GitHubError) || creation.status !== 422) throw creation;
      }
      existing = await this.resultRequest("/repos/" + full + "/git/ref/heads/" + ref, token);
    }
    if (existing?.object?.sha !== commit.sha) throw new GitHubError("result_conflict");
    return "github:" + full + "@" + commit.sha;
  }

  async archiveDownload(organization: string, repository: string, commit: string): Promise<string> {
    const full = target(organization, repository);
    if (!SHA.test(commit)) throw new GitHubError("invalid_source_commit");
    // GitHub archive responses redirect to a temporary codeload URL. Do not
    // forward the installation Authorization header to that destination.
    const response = await this.fetcher(API + "/repos/" + full + "/zipball/" + commit, {
      redirect: "manual",
      signal: AbortSignal.timeout(30000),
      headers: {
        authorization: "Bearer " + await this.installationToken(organization),
        accept: "application/vnd.github+json",
        "user-agent": "Agentic-Observer26",
      },
    });
    await response.body?.cancel();
    const location = response.headers.get("location");
    if (response.status !== 302 || !location) throw new GitHubError("archive_not_ready", response.status);
    const url = new URL(location);
    if (url.protocol !== "https:" || url.hostname !== "codeload.github.com" || url.username || url.password) {
      throw new GitHubError("unexpected_archive_destination");
    }
    return url.href;
  }
}

export type WorkflowIdentity = {
  repositoryId: string;
  /** observer-control (private, default) or the public pool repository. */
  repository?: string;
  visibility?: "private" | "public";
  organizationId: string;
  organization: string;
  workflow: "observer-prepare.yml" | "observer-execute.yml" | "observer-engine.yml" | "observer-score.yml";
  approvedSha: string;
  runId?: string;
  runAttempt?: string;
};
// Each cold isolate fetches GitHub's key set again; jose's default 5 s timeout
// was occasionally too short from the edge (2026-10-04 job_http_401 incidents).
const actionsKeys = createRemoteJWKSet(new URL("https://token.actions.githubusercontent.com/.well-known/jwks"), {
  timeoutDuration: 15000,
});

/** GitHub's key set could not be fetched: not the token's fault, so retryable (503), never 401. */
function keySetUnavailable(error: unknown) {
  return !(error instanceof errors.JOSEError) || error.code === "ERR_JWKS_TIMEOUT" ||
    error.code === "ERR_JOSE_GENERIC";
}

export async function verifyWorkflowIdentity(
  token: string,
  expected: WorkflowIdentity,
  keys: JWTVerifyGetKey = actionsKeys,
) {
  const name = expected.repository ?? CONTROL_REPOSITORY, visibility = expected.visibility ?? "private";
  if (
    !isRunnerOrganization(expected.organization) || !SHA.test(expected.approvedSha) ||
    !(name === CONTROL_REPOSITORY && visibility === "private" ||
      name === PUBLIC_POOL_REPOSITORY && visibility === "public")
  ) {
    throw new GitHubError("invalid_workflow_configuration");
  }
  let claims;
  try {
    ({ payload: claims } = await jwtVerify(token, keys, {
      algorithms: ["RS256"],
      issuer: "https://token.actions.githubusercontent.com",
      audience: "agentic-observer26",
      maxTokenAge: "10m",
      clockTolerance: 5,
      requiredClaims: ["exp", "iat", "jti", "sub"],
    }));
  } catch (error) {
    if (keySetUnavailable(error)) {
      console.error("observer-github: OIDC key set unavailable", (error as { code?: string }).code ?? "network");
      throw new GitHubError("workflow_identity_unavailable", 503);
    }
    throw new GitHubError("invalid_workflow_identity", 401);
  }
  const repo = expected.organization + "/" + name;
  // The public pool runs only its approved runtime tag.
  const allowedRef = (claims.ref === "refs/heads/main" && visibility === "private") ||
    claims.ref === "refs/tags/observer-runtime-" + expected.approvedSha;
  if (
    claims.repository_id !== expected.repositoryId || claims.repository_owner_id !== expected.organizationId ||
    claims.repository !== repo || claims.repository_visibility !== visibility ||
    claims.workflow_ref !== repo + "/.github/workflows/" + expected.workflow + "@" + claims.ref ||
    claims.workflow_sha !== expected.approvedSha || claims.sha !== expected.approvedSha ||
    !allowedRef || claims.event_name !== "workflow_dispatch" ||
    (expected.runId !== undefined && claims.run_id !== expected.runId) ||
    (expected.runAttempt !== undefined && claims.run_attempt !== expected.runAttempt) ||
    typeof claims.run_id !== "string" || !/^\d+$/.test(claims.run_id) ||
    typeof claims.run_attempt !== "string" || !/^\d+$/.test(claims.run_attempt)
  ) {
    throw new GitHubError("workflow_identity_mismatch", 403);
  }
  return { runId: claims.run_id, runAttempt: claims.run_attempt, subject: claims.sub };
}
