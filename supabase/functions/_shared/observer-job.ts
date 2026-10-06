import { boundedJson, decryptCredential, ProxyError } from "./observer-model.ts";
import type { Rpc } from "./observer-model.ts";
import { GitHubError, RUNNER_ORGANIZATION_PATTERN, verifyWorkflowIdentity } from "./observer-github.ts";
import type { WorkflowIdentity } from "./observer-github.ts";
import { AGENT_LOG_BYTES } from "./observer-agent-log.ts";
import {
  decodeKey,
  encodeKey,
  openSealed,
  publicKeyFor,
  seal,
  SEAL_OVERHEAD,
  SealError,
  toBase64,
} from "./observer-seal.ts";
import { readZipFiles, ZipError } from "./observer-zip.ts";

const PARTICIPANT_REPOSITORY = new RegExp("^" + RUNNER_ORGANIZATION_PATTERN + "\\/participant-[0-9a-f]{32}$");

export type JobDependencies = {
  rpc: Rpc;
  masterKey: string;
  verify?: typeof verifyWorkflowIdentity;
  repositoryCredentials?: (user: string, kind: "prepare" | "engine") => Promise<{ full_name: string; token: string }>;
  archiveDownload?: (reference: string, privateOnly: boolean) => Promise<string>;
  scenarioDownload?: (path: string) => Promise<string>;
  sourceDownload?: (path: string, bucket?: "observer-staging" | "observer-sources") => Promise<string>;
  /** Stores the scrubbed participant log beside the run's private result. */
  storeAgentLog?: (run: string, log: Uint8Array) => Promise<void>;
  /** Public runner pool only (ops/public-runner-pool.md). */
  publicPool?: PublicPoolDependencies;
  /**
   * Egress route nodes (Supabase secret OBSERVER_EGRESS_ROUTES; ops/egress-routes.md),
   * added to a job's team_egress.route at claim time only, never stored in a job input.
   */
  egressRoutes?: EgressRoutes;
};

export type EgressRoutes = { cn: Record<string, unknown>[]; overseas: Record<string, unknown>[] };

const ROUTE_NODE_FIELDS: Record<string, string[]> = {
  vless: ["type", "server", "server_port", "uuid", "flow", "tls", "packet_encoding"],
  hysteria2: ["type", "server", "server_port", "password", "tls", "up_mbps", "down_mbps", "obfs"],
};

/** The node list of OBSERVER_EGRESS_ROUTES (sing-box outbounds); throws on any other shape. */
export function parseEgressRoutes(text: string): EgressRoutes {
  const value = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid");
  const out: EgressRoutes = { cn: [], overseas: [] };
  for (const name of ["cn", "overseas"] as const) {
    const nodes = value[name] ?? [];
    if (!Array.isArray(nodes) || nodes.length > (name === "cn" ? 1 : 4)) throw new Error("invalid");
    for (const node of nodes) {
      if (!validRouteNode(node)) throw new Error("invalid");
    }
    out[name] = nodes;
  }
  return out;
}

function validRouteNode(node: unknown) {
  if (!node || typeof node !== "object" || Array.isArray(node)) return false;
  const value = node as Record<string, unknown>;
  const fields = ROUTE_NODE_FIELDS[String(value.type)];
  return !!fields && Object.keys(value).every((key) => fields.includes(key)) &&
    typeof value.server === "string" && /^[A-Za-z0-9.:\[\]-]{1,253}$/.test(value.server) &&
    Number.isInteger(value.server_port) && Number(value.server_port) > 0 && Number(value.server_port) < 65536 &&
    !!value.tls && typeof value.tls === "object" && (value.tls as Record<string, unknown>).enabled === true &&
    JSON.stringify(value).length <= 4096;
}

/**
 * The public-repository pool's sealed transfers. A public run's inputs and
 * logs are visible to anyone, so nothing private reaches it in clear text:
 * the scenario and project bytes are sealed to the job's in-memory runner key
 * and staged as ciphertext, the claim payload is sealed the same way, and the
 * result arrives sealed to resultKey, whose private half only this backend has.
 */
export type PublicPoolDependencies = {
  /** X25519 private key (OBSERVER_RESULT_SEAL_KEY); results are sealed to its public key. */
  resultKey: Uint8Array;
  readScenario: (path: string) => Promise<Uint8Array>;
  readArchive: (reference: string) => Promise<Uint8Array>;
  /** Stores ciphertext at sealed/<job>/<name>.zip and returns a short-lived download URL. */
  stage: (job: string, name: "scenario" | "project" | "trace", sealed: Uint8Array) => Promise<string>;
  /** A fresh short-lived signed upload URL for sealed/<job>/result.zip, issued just before upload. */
  resultUpload: (job: string) => Promise<{ url: string; path: string }>;
  readResult: (job: string) => Promise<Uint8Array>;
  commitResult: (
    user: string,
    run: string,
    files: { path: string; data: Uint8Array; executable: boolean }[],
  ) => Promise<string>;
};

const RESULT_EXPANDED_BYTES = 100 * 1024 * 1024;
// The staging bucket's object limit; a sealed object is SEAL_OVERHEAD larger than its input.
const STAGED_BYTES = 50 * 1024 * 1024;

async function sealedInput(runnerKey: Uint8Array, data: Uint8Array, context: string) {
  if (data.length + SEAL_OVERHEAD > STAGED_BYTES) throw new ProxyError(503, "sealed_input_too_large");
  return await seal(runnerKey, data, context);
}

const RECEIPT_BODY_LIMIT = 1100000;
const AGENT_LOG_BODY_LIMIT = 3 * 1024 * 1024;

function validateArtifactUpload(value: unknown, id: string, filename: string, sealedJob?: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ProxyError(503, "invalid_job_payload");
  const upload = value as Record<string, unknown>;
  if (sealedJob !== undefined) {
    // Public pool: the sealed result goes to the backend's staging object only;
    // its upload URL is issued when the result is ready (result_upload).
    if (
      Object.keys(upload).sort().join(",") !== "kind,path" || upload.kind !== "sealed" ||
      upload.path !== "sealed/" + sealedJob + "/result.zip"
    ) throw new ProxyError(503, "invalid_job_payload");
    return;
  }
  if (Object.keys(upload).join(",") === "kind" && upload.kind === "github") return;
  if (
    Object.keys(upload).sort().join(",") !== "path,url" || typeof upload.url !== "string" ||
    typeof upload.path !== "string" ||
    !new RegExp("^[0-9a-f-]{36}/" + id + "/" + filename + "\\.zip$").test(upload.path)
  ) {
    throw new ProxyError(503, "invalid_job_payload");
  }
  let url: URL;
  try {
    url = new URL(upload.url);
  } catch {
    throw new ProxyError(503, "invalid_job_payload");
  }
  if (url.protocol !== "https:" || url.username || url.password || url.hash || url.port) {
    throw new ProxyError(503, "invalid_job_payload");
  }
}

// Payload classes are deliberately separate. An executor can never receive the
// engine's scenario bundle, a service key, or repository installation credentials.
export function validateJobPayload(payload: unknown, expected: WorkflowIdentity, job: string) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ProxyError(503, "invalid_job_payload");
  }
  const value = payload as Record<string, unknown>;
  const kind = expected.workflow.slice("observer-".length, -".yml".length);
  const fields: Record<string, string[]> = {
    execute: [
      "kind",
      "job_id",
      "run_id",
      "archive_url",
      "source_digest",
      "manifest",
      "session_url",
      "run_credential",
      "model_base_url",
      "team_egress",
      "model_disabled",
    ],
    engine: [
      "kind",
      "job_id",
      "run_id",
      "scenario_url",
      "scenario_digest",
      "session_url",
      "run_credential",
      "runtime_seconds",
      "artifact_upload",
      "instance",
      "archive_url",
      "colocated",
      "restricted_egress",
      "result_key",
      "team_egress",
      "model_disabled",
    ],
    // The independent rescore: the scenario and the run's stored result, no
    // session capability (a score job can never publish or finish a session).
    score: [
      "kind",
      "job_id",
      "run_id",
      "scenario_url",
      "scenario_digest",
      "result_url",
      "decisions_digest",
      "termination_reason",
    ],
    prepare: [
      "kind",
      "job_id",
      "revision_id",
      "archive_url",
      "source_digest",
      "repository",
      "artifact_upload",
      "model_base_url",
      "run_credential",
      "model",
      "gameplay",
      "team_egress",
    ],
  };
  if (
    !fields[kind] || value.kind !== kind || value.job_id !== job ||
    Object.keys(value).some((key) => !fields[kind].includes(key))
  ) throw new ProxyError(503, "invalid_job_payload");
  // The public pool takes colocated public-scenario engine jobs, always with a
  // sealed result, and score jobs (sealed inputs, no result upload); a
  // private-pool job never carries sealing fields.
  const sealed = expected.visibility === "public";
  if (
    sealed && (kind === "engine"
        ? value.colocated === undefined || value.instance !== undefined ||
          typeof value.result_key !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.result_key)
        : kind !== "score" || value.result_key !== undefined) ||
    !sealed && value.result_key !== undefined
  ) throw new ProxyError(503, "invalid_job_payload");
  const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
  const hash = /^[0-9a-f]{64}$/;
  const string = (name: string, pattern?: RegExp) => {
    const field = value[name];
    if (typeof field !== "string" || !field || (pattern && !pattern.test(field))) {
      throw new ProxyError(503, "invalid_job_payload");
    }
    return field;
  };
  const url = (name: string) => {
    let parsed: URL;
    try {
      parsed = new URL(string(name));
    } catch {
      throw new ProxyError(503, "invalid_job_payload");
    }
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash || parsed.port) {
      throw new ProxyError(503, "invalid_job_payload");
    }
  };
  if (kind === "execute" || kind === "engine") {
    const run = string("run_id", uuid);
    string("run_credential", new RegExp("^obs_" + run + "\\.[A-Za-z0-9_-]{40,100}$"));
    url("session_url");
  }
  const projectManifest = (manifest: Record<string, unknown> | null) => {
    if (
      !manifest || typeof manifest !== "object" || Array.isArray(manifest) ||
      manifest.schema_version !== "observer-project-v1" ||
      typeof manifest.image !== "string" ||
      !/^[a-zA-Z0-9][a-zA-Z0-9._/:-]*@sha256:[0-9a-f]{64}$/.test(manifest.image) ||
      !Array.isArray(manifest.run) || !manifest.run.length ||
      !manifest.run.every((arg) => typeof arg === "string" && arg.length > 0 && !arg.includes("\0"))
    ) {
      throw new ProxyError(503, "invalid_job_payload");
    }
  };
  if (kind === "execute") {
    url("archive_url");
    url("model_base_url");
    string("source_digest", hash);
    projectManifest(value.manifest as Record<string, unknown> | null);
  }
  if (kind === "engine" && value.colocated !== undefined) {
    // A public-scenario run whose participant container starts inside the
    // engine job; it carries the execute job's fields under "colocated".
    const run = string("run_id", uuid);
    url("archive_url");
    const colocated = value.colocated as Record<string, unknown> | null;
    if (
      !colocated || typeof colocated !== "object" || Array.isArray(colocated) || value.instance !== undefined ||
      Object.keys(colocated).sort().join(",") !== "manifest,model_base_url,run_credential,source_digest" ||
      typeof colocated.run_credential !== "string" ||
      !new RegExp("^obs_" + run + "\\.[A-Za-z0-9_-]{40,100}$").test(colocated.run_credential) ||
      colocated.run_credential === value.run_credential ||
      typeof colocated.source_digest !== "string" || !hash.test(colocated.source_digest) ||
      typeof colocated.model_base_url !== "string" || !colocated.model_base_url.startsWith("https://")
    ) {
      throw new ProxyError(503, "invalid_job_payload");
    }
    projectManifest(colocated.manifest as Record<string, unknown> | null);
  } else if (kind === "engine" && (value.archive_url !== undefined || value.restricted_egress !== undefined)) {
    throw new ProxyError(503, "invalid_job_payload");
  }
  // Team egress: the team's variables and allowed domains (project_platform/team_egress.py).
  if (value.team_egress !== undefined) {
    if (kind === "engine" && value.colocated === undefined) throw new ProxyError(503, "invalid_job_payload");
    validateTeamEgress(value.team_egress);
  }
  // An evaluation without a model: only `true`, and only where a participant container runs.
  if (
    value.model_disabled !== undefined &&
    (value.model_disabled !== true || (kind === "engine" && value.colocated === undefined))
  ) throw new ProxyError(503, "invalid_job_payload");
  // Model-proxy-only egress for the colocated participant container.
  if (kind === "engine" && value.restricted_egress !== undefined && value.restricted_egress !== true) {
    throw new ProxyError(503, "invalid_job_payload");
  }
  if (kind === "score") {
    string("run_id", uuid);
    url("scenario_url");
    url("result_url");
    string("scenario_digest", hash);
    string("decisions_digest", hash);
    if (typeof value.termination_reason !== "string" || !/^[a-z_]{0,64}$/.test(value.termination_reason)) {
      throw new ProxyError(503, "invalid_job_payload");
    }
  }
  if (kind === "engine") {
    url("scenario_url");
    string("scenario_digest", hash);
    if (
      !Number.isInteger(value.runtime_seconds) || Number(value.runtime_seconds) < 10 ||
      Number(value.runtime_seconds) > 18000
    ) {
      throw new ProxyError(503, "invalid_job_payload");
    }
    validateArtifactUpload(value.artifact_upload, string("run_id", uuid), "result", sealed ? job : undefined);
    if (value.instance !== undefined) {
      const instance = value.instance as Record<string, unknown>;
      if (
        !instance || typeof instance !== "object" || Array.isArray(instance) ||
        Object.keys(instance).sort().join(",") !== "bundle_digest,max_candidates,profile,profile_id,seed" ||
        typeof instance.seed !== "string" || !hash.test(instance.seed) ||
        typeof instance.profile_id !== "string" || !uuid.test(instance.profile_id) ||
        instance.bundle_digest !== value.scenario_digest || instance.max_candidates !== 32 ||
        !instance.profile || typeof instance.profile !== "object" || Array.isArray(instance.profile) ||
        JSON.stringify(instance.profile).length > 65536
      ) {
        throw new ProxyError(503, "invalid_job_payload");
      }
    }
  }
  if (kind === "prepare") {
    const revision = string("revision_id", uuid);
    url("archive_url");
    // A repository upload has an immutable Git commit; a ZIP additionally has
    // its uploaded archive digest. The preparation worker calculates source hash.
    if (value.source_digest !== null && value.source_digest !== undefined) string("source_digest", hash);
    const repository = value.repository as Record<string, unknown> | null;
    if (
      !repository || typeof repository !== "object" || Object.keys(repository).sort().join(",") !== "full_name,token" ||
      typeof repository.full_name !== "string" ||
      !PARTICIPANT_REPOSITORY.test(repository.full_name) ||
      !repository.full_name.startsWith(expected.organization + "/") ||
      typeof repository.token !== "string" || !repository.token || /[\r\n\0]/.test(repository.token)
    ) {
      throw new ProxyError(503, "invalid_job_payload");
    }
    validateArtifactUpload(value.artifact_upload, revision, "preview");
    if (value.gameplay !== undefined && value.gameplay !== "v4") throw new ProxyError(503, "invalid_job_payload");
    // Direct model access (team variables) or the model proxy, never both.
    if (
      value.team_egress !== undefined && (value.model !== undefined || value.model_base_url !== undefined ||
        value.run_credential !== undefined)
    ) throw new ProxyError(503, "invalid_job_payload");
    if (value.model !== undefined || value.model_base_url !== undefined || value.run_credential !== undefined) {
      string("model");
      url("model_base_url");
      string("run_credential", /^obs_[0-9a-f-]{36}\.[A-Za-z0-9_-]{40,100}$/);
    }
  }
  return value;
}

const VARIABLE_NAME = /^[A-Z][A-Z0-9_]{0,63}$/;
const RESERVED_VARIABLES = new Set([
  "HTTPS_PROXY",
  "HTTP_PROXY",
  "ALL_PROXY",
  "NO_PROXY",
  "NODE_USE_ENV_PROXY",
  "PATH",
  "HOME",
  "HOSTNAME",
  "LD_PRELOAD",
  "LD_LIBRARY_PATH",
  "PYTHONPATH",
  "NODE_OPTIONS",
]);
const DOMAIN = /^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$/;
const RESERVED_SUFFIX = /(^|\.)(localhost|local|internal|intranet|lan|home\.arpa|arpa|test|example|invalid|onion)$/;

export function validTeamVariableName(name: string) {
  return VARIABLE_NAME.test(name) && !/^(OBSERVER_|SAC_)/.test(name) && !name.endsWith("_PROXY") &&
    !RESERVED_VARIABLES.has(name);
}
export function validTeamDomain(host: unknown) {
  return typeof host === "string" && DOMAIN.test(host) && !/^[\d.]+$/.test(host) && !RESERVED_SUFFIX.test(host);
}

function validateTeamEgress(value: unknown) {
  const egress = value as Record<string, unknown> | null;
  const invalid = () => new ProxyError(503, "invalid_job_payload");
  if (
    !egress || typeof egress !== "object" || Array.isArray(egress) ||
    !["domains,environment,secrets", "domains,environment,open,secrets", "domains,environment,open,route,secrets"]
      .includes(Object.keys(egress).sort().join(",")) ||
    (egress.open !== undefined && (egress.open !== true || !Array.isArray(egress.domains) || egress.domains.length))
  ) throw invalid();
  const environment = egress.environment as Record<string, unknown> | null;
  if (!environment || typeof environment !== "object" || Array.isArray(environment)) throw invalid();
  const names = Object.keys(environment);
  if (
    names.length > 20 ||
    names.some((name) => {
      const text = environment[name];
      return !validTeamVariableName(name) || typeof text !== "string" || text.includes("\0") ||
        new TextEncoder().encode(text).length > 8192;
    })
  ) throw invalid();
  if (egress.route !== undefined) {
    // Labels and limits from the job input, plus the nodes the claim added (project_platform/team_egress.py).
    const route = egress.route as Record<string, unknown> | null;
    if (
      !route || typeof route !== "object" || Array.isArray(route) ||
      !["cap_bytes,fallback,name", "cap_bytes,fallback,name,nodes"].includes(Object.keys(route).sort().join(",")) ||
      (route.name !== "cn" && route.name !== "overseas") || typeof route.fallback !== "boolean" ||
      !Number.isSafeInteger(route.cap_bytes) || Number(route.cap_bytes) <= 0 ||
      (route.nodes !== undefined &&
        (!Array.isArray(route.nodes) || route.nodes.length > (route.name === "cn" ? 1 : 4) ||
          !route.nodes.every(validRouteNode)))
    ) throw invalid();
  }
  const secrets = egress.secrets, domains = egress.domains;
  if (
    !Array.isArray(secrets) || secrets.some((name) => typeof name !== "string" || !names.includes(name)) ||
    !Array.isArray(domains) || domains.length > 10 || new Set(domains).size !== domains.length ||
    !domains.every(validTeamDomain)
  ) throw invalid();
}

export async function jobRequest(request: Request, deps: JobDependencies) {
  const bearer = /^Bearer ([A-Za-z0-9_.-]+)$/.exec(request.headers.get("authorization") ?? "");
  if (!bearer) throw new ProxyError(401, "workflow_identity_required");
  const body = await boundedJson(request, AGENT_LOG_BODY_LIMIT);
  if (
    !body || typeof body !== "object" || Array.isArray(body) ||
    typeof body.job_id !== "string" || !/^[0-9a-f-]{36}$/.test(body.job_id) ||
    !["claim", "complete", "artifact_repository", "agent_log", "result_upload", "store_result"].includes(body.action)
  ) throw new ProxyError(400, "invalid_job_request");
  // Only the participant log may use the larger request size.
  if (body.action !== "agent_log" && JSON.stringify(body).length > RECEIPT_BODY_LIMIT) {
    throw new ProxyError(413, "body_too_large");
  }
  const identity = await deps.rpc("observer_job_identity", { p_job: body.job_id });
  if (!identity) throw new ProxyError(404, "job_unavailable");
  const expected: WorkflowIdentity = {
    ...identity,
    repository: identity.repository ?? undefined,
    visibility: identity.visibility ?? undefined,
    runId: identity.runId ?? undefined,
    runAttempt: identity.runAttempt ?? undefined,
  };
  const verified = await (deps.verify ?? verifyWorkflowIdentity)(bearer[1], expected);
  const isPublic = expected.visibility === "public";
  if (isPublic && (!deps.publicPool || !["claim", "complete", "result_upload", "store_result"].includes(body.action))) {
    throw new ProxyError(403, "public_pool_action_denied");
  }
  if (!isPublic && ["result_upload", "store_result"].includes(body.action)) {
    throw new ProxyError(403, "artifact_access_denied");
  }
  const repository = async () => {
    if (expected.workflow === "observer-execute.yml" || !deps.repositoryCredentials) {
      throw new ProxyError(403, "artifact_access_denied");
    }
    const target = await deps.rpc("observer_job_artifact_target", {
      p_job: body.job_id,
      p_github_run: verified.runId,
      p_attempt: verified.runAttempt,
    });
    if (!target?.user_id || !target?.artifact_id || !["prepare", "engine"].includes(target.kind)) {
      throw new ProxyError(403, "artifact_access_denied");
    }
    return {
      ...await deps.repositoryCredentials(target.user_id, target.kind),
      artifact_id: target.artifact_id,
      kind: target.kind,
    };
  };
  const claimedInput = async () => {
    // A public-pool job has no nonce (its dispatch inputs are public): the
    // database binds its claim to the run id GitHub returned at dispatch.
    if (
      isPublic
        ? body.nonce !== undefined
        : typeof body.nonce !== "string" || !/^[A-Za-z0-9_-]{40,100}$/.test(body.nonce)
    ) {
      throw new ProxyError(400, "invalid_job_nonce");
    }
    // Idempotent for the GitHub run that already claimed the job; any other
    // run, nonce, workflow or finished job is refused by the database.
    const ciphertext = await deps.rpc("observer_claim_job", {
      p_job: body.job_id,
      p_nonce: isPublic ? null : body.nonce,
      p_github_run: verified.runId,
      p_attempt: verified.runAttempt,
      p_repository: expected.repositoryId,
      p_owner: expected.organizationId,
      p_sha: expected.approvedSha,
    });
    return await decryptCredential(ciphertext, body.job_id, deps.masterKey);
  };
  if (body.action === "artifact_repository") return await repository();
  if (body.action === "result_upload") {
    await claimedEngine(body.job_id, verified, deps);
    return await deps.publicPool!.resultUpload(body.job_id);
  }
  if (body.action === "store_result") return await storeSealedResult(deps.publicPool!, body.job_id, verified, deps);
  if (body.action === "agent_log") {
    // Only the executor has participant output. The run is taken from the
    // claimed job's own encrypted input, never from the request.
    if (expected.workflow !== "observer-execute.yml" || !deps.storeAgentLog) {
      throw new ProxyError(403, "agent_log_denied");
    }
    if (typeof body.log !== "string" || body.log.length > Math.ceil(AGENT_LOG_BYTES / 3) * 4) {
      throw new ProxyError(400, "invalid_agent_log");
    }
    let input;
    try {
      input = JSON.parse(await claimedInput());
    } catch (error) {
      if (error instanceof ProxyError) throw error;
      throw new ProxyError(503, "invalid_job_payload");
    }
    const run = input?.run_id;
    if (
      input?.kind !== "execute" || input?.job_id !== body.job_id || typeof run !== "string" ||
      !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(run)
    ) throw new ProxyError(403, "agent_log_denied");
    let raw: Uint8Array;
    try {
      raw = Uint8Array.from(atob(body.log), (c) => c.charCodeAt(0));
    } catch {
      throw new ProxyError(400, "invalid_agent_log");
    }
    if (raw.length > AGENT_LOG_BYTES) throw new ProxyError(400, "invalid_agent_log");
    // Defense in depth: the runner already scrubbed its own capability.
    let text = new TextDecoder().decode(raw);
    if (typeof input.run_credential === "string" && input.run_credential) {
      text = text.replaceAll(input.run_credential, "[REDACTED]");
    }
    await deps.storeAgentLog(run, new TextEncoder().encode(text));
    return { accepted: true };
  }
  if (body.action === "claim") {
    let runnerKey: Uint8Array | undefined;
    if (isPublic) {
      try {
        runnerKey = decodeKey(body.runner_key);
      } catch {
        throw new ProxyError(400, "invalid_runner_key");
      }
    }
    const cleartext = await claimedInput();
    let parsed;
    try {
      parsed = JSON.parse(cleartext);
    } catch {
      throw new ProxyError(503, "invalid_job_payload");
    }
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      if (parsed.archive_storage_ref !== undefined) {
        const ref = parsed.archive_storage_ref;
        if (
          parsed.kind !== "prepare" || parsed.archive_url !== undefined || parsed.archive_ref !== undefined ||
          !deps.sourceDownload || !ref || typeof ref !== "object" || Array.isArray(ref) ||
          Object.keys(ref).sort().join(",") !== "bucket,path" || typeof ref.path !== "string" ||
          !(ref.bucket === "observer-staging" && /^[0-9a-f-]{36}\/[0-9a-f-]{36}\/source[.]zip$/.test(ref.path) ||
            // A repository snapshot stored at submission (observer_record_source_snapshot).
            ref.bucket === "observer-sources" && /^[0-9a-f-]{36}\/[0-9a-f]{40}-[0-9a-f-]{36}[.]zip$/.test(ref.path))
        ) {
          throw new ProxyError(503, "invalid_job_payload");
        }
        parsed.archive_url = await deps.sourceDownload(ref.path, ref.bucket);
        delete parsed.archive_storage_ref;
      }
      if (parsed.scenario_ref !== undefined) {
        const ref = parsed.scenario_ref;
        if (
          (parsed.kind !== "engine" && parsed.kind !== "score") || parsed.scenario_url !== undefined ||
          !deps.scenarioDownload ||
          !ref || typeof ref !== "object" || Array.isArray(ref) ||
          Object.keys(ref).sort().join(",") !== "bucket,path" || ref.bucket !== "observer-scenarios" ||
          typeof ref.path !== "string" || !/^[A-Za-z0-9_/-]+[.]zip$/.test(ref.path)
        ) {
          throw new ProxyError(503, "invalid_job_payload");
        }
        parsed.scenario_url = runnerKey
          ? await deps.publicPool!.stage(
            body.job_id,
            "scenario",
            await sealedInput(runnerKey, await deps.publicPool!.readScenario(ref.path), "scenario:" + body.job_id),
          )
          : await deps.scenarioDownload(ref.path);
        delete parsed.scenario_ref;
      }
      if (parsed.result_ref !== undefined) {
        // The trace a score job recomputes: the run's private result snapshot,
        // exactly as the finish call recorded it.
        if (
          parsed.kind !== "score" || parsed.result_url !== undefined || !deps.archiveDownload ||
          typeof parsed.result_ref !== "string"
        ) {
          throw new ProxyError(503, "invalid_job_payload");
        }
        parsed.result_url = runnerKey
          ? await deps.publicPool!.stage(
            body.job_id,
            "trace",
            await sealedInput(runnerKey, await deps.publicPool!.readArchive(parsed.result_ref), "trace:" + body.job_id),
          )
          : await deps.archiveDownload(parsed.result_ref, true);
        delete parsed.result_ref;
      }
      if (parsed.archive_ref !== undefined) {
        if (parsed.archive_url !== undefined || !deps.archiveDownload || typeof parsed.archive_ref !== "string") {
          throw new ProxyError(503, "invalid_job_payload");
        }
        parsed.archive_url = runnerKey
          ? await deps.publicPool!.stage(
            body.job_id,
            "project",
            await sealedInput(
              runnerKey,
              await deps.publicPool!.readArchive(parsed.archive_ref),
              "project:" + body.job_id,
            ),
          )
          : await deps.archiveDownload(parsed.archive_ref, parsed.kind !== "prepare");
        delete parsed.archive_ref;
      }
      if (parsed.kind === "prepare" && parsed.repository && !parsed.repository.token) {
        const fresh = await repository();
        if (fresh.full_name !== parsed.repository.full_name || fresh.artifact_id !== parsed.revision_id) {
          throw new ProxyError(503, "invalid_job_payload");
        }
        parsed.repository = { full_name: fresh.full_name, token: fresh.token };
      }
    }
    const team = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed.team_egress : undefined;
    if (team && typeof team === "object" && !Array.isArray(team) && team.route !== undefined) {
      // The stored input names the route only; its nodes join the payload here, for the
      // run's own sidecar (sealed to the job's key in the public pool). No nodes configured:
      // the runner falls back to direct (or refuses) as the team chose.
      const route = team.route;
      if (
        !["execute", "engine"].includes(parsed.kind) || !route || typeof route !== "object" ||
        Array.isArray(route) || route.nodes !== undefined || (route.name !== "cn" && route.name !== "overseas")
      ) throw new ProxyError(503, "invalid_job_payload");
      team.route = { ...route, nodes: deps.egressRoutes?.[route.name as "cn" | "overseas"] ?? [] };
    }
    if (runnerKey && parsed && typeof parsed === "object" && !Array.isArray(parsed) && parsed.kind === "engine") {
      parsed.artifact_upload = { kind: "sealed", path: "sealed/" + body.job_id + "/result.zip" };
      parsed.result_key = encodeKey(publicKeyFor(deps.publicPool!.resultKey));
    }
    const payload = validateJobPayload(parsed, expected, body.job_id);
    if (!runnerKey) return payload;
    const sealedPayload = await seal(
      runnerKey,
      new TextEncoder().encode(JSON.stringify(payload)),
      "claim:" + body.job_id,
    );
    return { sealed: toBase64(sealedPayload) };
  }
  await deps.rpc("observer_finish_job", {
    p_job: body.job_id,
    p_github_run: verified.runId,
    p_attempt: verified.runAttempt,
    p_result: body.result,
    p_error: typeof body.error === "string" ? body.error : "",
  });
  return { accepted: true };
}

/**
 * Open the public-pool result the claimed run uploaded, commit it to the
 * team's private repository and return its result path for session finish.
 * Only the run that holds the claim can store, and only its own run's result.
 */
async function claimedEngine(job: string, verified: { runId: string; runAttempt: string }, deps: JobDependencies) {
  const target = await deps.rpc("observer_job_artifact_target", {
    p_job: job,
    p_github_run: verified.runId,
    p_attempt: verified.runAttempt,
  });
  if (!target?.user_id || !target?.artifact_id || target.kind !== "engine") {
    throw new ProxyError(403, "artifact_access_denied");
  }
  return target as { user_id: string; artifact_id: string };
}

async function storeSealedResult(
  pool: PublicPoolDependencies,
  job: string,
  verified: { runId: string; runAttempt: string },
  deps: JobDependencies,
) {
  const target = await claimedEngine(job, verified, deps);
  let files;
  try {
    const archive = await openSealed(pool.resultKey, await pool.readResult(job), "result:" + job);
    files = await readZipFiles(archive, RESULT_EXPANDED_BYTES);
  } catch (error) {
    if (error instanceof SealError || error instanceof ZipError) throw new ProxyError(400, "invalid_job_result");
    throw error;
  }
  try {
    return { result_path: await pool.commitResult(target.user_id, target.artifact_id, files) };
  } catch (error) {
    // Codes and statuses only: never paths, tokens or result contents.
    const code = error instanceof GitHubError || error instanceof ProxyError ? error.code : "unexpected";
    console.error("observer-job: public result commit failed", code, (error as { status?: number }).status ?? 0);
    // A GitHub status (e.g. 500) is not this API's status: the runner retries 503.
    throw new ProxyError(503, "result_commit_unavailable");
  }
}
