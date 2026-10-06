import { fulfillPersonalModel } from "./observer-personal-model.ts";
import { sendModelBroadcast } from "./observer-model-broadcast.ts";
import { boundedJson, decryptCredential, encryptCredential, ProxyError } from "./observer-model.ts";
import { publicBase, type Resolver } from "./observer-public-base.ts";
import type { SupabaseClient } from "npm:@supabase/supabase-js@2";
import { GitHubError, parseSourceUrl, sourceRef, sourceSubdir } from "./observer-github.ts";
import { filesZip, readZipFiles, ZipError, zipNames } from "./observer-zip.ts";
import { inspectSourceNames } from "./observer-source-check.ts";
import {
  agentLogView,
  fetchArchive,
  RESULT_ARCHIVE_BYTES,
  resultAgentLog,
  resultCopy,
  resultCopyPath,
  type ResultStorage,
  storedAgentLog,
} from "./observer-agent-log.ts";
import { validTeamDomain, validTeamVariableName } from "./observer-job.ts";

type Dependencies = {
  user: SupabaseClient;
  service: SupabaseClient;
  userId: string;
  masterKey: string;
  modelBases: string[];
  httpBases: string[];
  /** DNS lookups for participant bases; tests replace it. */
  resolve?: Resolver | null;
  artifactDownload?: (reference: string) => Promise<string>;
  /** Fetches signed result archives; tests replace it. */
  fetchArchive?: typeof fetch;
  /**
   * Resolves a public repository URL (and the optional branch/tag/commit and folder)
   * to its exact commit and that commit's zipball URL (GitHub App).
   */
  resolveSource?: (
    url: string,
    options: { ref?: unknown; subdir?: unknown },
  ) => Promise<{ commit: string; archiveUrl: string; ref?: string | null; subdir?: string | null }>;
};
// A repository submission is preserved as the zipball of its exact commit (bucket
// observer-sources; nothing deletes from it). Larger sources must be uploaded as ZIP.
export const SOURCE_SNAPSHOT_LIMIT = 104857600;
async function sha256Hex(bytes: Uint8Array<ArrayBuffer>) {
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return Array.from(hash, (n) => n.toString(16).padStart(2, "0")).join("");
}
async function boundedDownload(url: string, fetcher: typeof fetch, limit: number): Promise<Uint8Array<ArrayBuffer>> {
  const response = await fetcher(url, { redirect: "follow", signal: AbortSignal.timeout(60000) });
  if (!response.ok || !response.body) {
    await response.body?.cancel();
    throw new ProxyError(503, "source_snapshot_unavailable");
  }
  let size = 0;
  const body = response.body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        size += chunk.length;
        if (size > limit) throw new ProxyError(400, "source_too_large");
        controller.enqueue(chunk);
      },
    }),
  );
  // A large archive goes through a temporary file: holding thousands of network
  // chunks in memory costs several times the archive size (each keeps its larger
  // buffer) and can exceed the function's memory limit. Without a writable temporary
  // directory (tests) every chunk is copied to its own size instead.
  let path: string | null = null;
  try {
    path = await Deno.makeTempFile({ prefix: "source-", suffix: ".zip" });
  } catch {
    path = null;
  }
  if (path) {
    try {
      const file = await Deno.open(path, { write: true, truncate: true });
      await body.pipeTo(file.writable);
      return await Deno.readFile(path);
    } finally {
      await Deno.remove(path).catch(() => {});
    }
  }
  const chunks: Uint8Array[] = [];
  for await (const chunk of body) chunks.push(chunk.slice());
  const out = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    out.set(chunk, at);
    at += chunk.length;
  }
  return out;
}
/**
 * The named folder of a GitHub zipball as its own archive. The archive's single top
 * folder is kept, so preparation (which drops exactly one such folder) sees the
 * chosen folder as the project root.
 */
export async function folderSnapshot(zip: Uint8Array<ArrayBuffer>, folder: string): Promise<Uint8Array<ArrayBuffer>> {
  try {
    const names = zipNames(zip);
    const top = names[0]?.includes("/") ? names[0].slice(0, names[0].indexOf("/") + 1) : "";
    if (!top || !names.every((n) => n.startsWith(top))) throw new ZipError("invalid_zip");
    const prefix = top + folder + "/";
    if (!names.some((n) => n.startsWith(prefix) && !n.endsWith("/"))) {
      throw new ProxyError(400, "source_subdir_not_found");
    }
    const files = await readZipFiles(zip, SOURCE_SNAPSHOT_LIMIT, 10000, (n) => n.startsWith(prefix));
    return await filesZip(files.map((f) => ({ ...f, path: top + f.path.slice(prefix.length) }))) as Uint8Array<
      ArrayBuffer
    >;
  } catch (error) {
    if (error instanceof ProxyError) throw error;
    if (error instanceof ZipError && error.message === "zip_entry_too_large") {
      throw new ProxyError(400, "source_too_large");
    }
    throw new ProxyError(400, "invalid_source_archive");
  }
}
const known = new Set([
  "team_required",
  "model_request_already_received",
  "request_id_conflict",
  "invalid_team_model",
  "invalid_team_model_mode",
  "competition_project_required",
  "projects_not_enabled",
  "invalid_repository_url",
  "invalid_upload_path",
  "preparation_limit",
  "preparation_daily_limit",
  "revision_not_found",
  "revision_not_ready",
  "stale_approval",
  "phase_closed",
  "local_sessions_disabled",
  "revision_not_approved",
  "revision_already_evaluated",
  "revision_withdrawn",
  "revision_not_withdrawable",
  "daily_limit",
  "batch_already_active",
  "no_scenarios",
  "run_not_found",
  "csv_does_not_match_session",
  "session_not_finished",
  "provider_limit",
  "provider_not_found",
  "invalid_provider",
  "upload_limit",
  "account_banned",
  "diagnostics_not_found",
  "final_phase_invalid",
  "final_version_locked",
  "invalid_team_variable",
  "team_variable_limit",
  "invalid_team_domains",
  "invalid_egress_route",
  "egress_route_unavailable",
  "team_domain_not_public",
]);
function failure(error: { message: string } | null) {
  if (error) throw new ProxyError(400, known.has(error.message) ? error.message : "portal_request_failed");
}
function uuid(value: unknown): string {
  if (typeof value !== "string" || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value)) {
    throw new ProxyError(400, "invalid_identifier");
  }
  return value;
}
function text(value: unknown, max: number, empty = false): string {
  if (typeof value !== "string" || value.length > max || (!empty && !value.trim())) {
    throw new ProxyError(400, "invalid_field");
  }
  return value.trim();
}
/** Recognition hint only: never more than the last four characters of a long key. */
export function keyHint(key: string): string {
  return key.length >= 16 ? key.slice(-4) : "";
}

export async function portalRequest(request: Request, d: Dependencies): Promise<unknown> {
  const body = await boundedJson(request, 98304);
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new ProxyError(400, "invalid_request");
  const userRpc = async (name: string, args: Record<string, unknown> = {}) => {
    const { data, error } = await d.user.rpc(name, args);
    failure(error);
    return data;
  };
  // Informational: a database without the function yet returns null instead of failing the page.
  const optionalUserRpc = async (name: string) => {
    const { data, error } = await d.user.rpc(name, {});
    return error ? null : data;
  };
  const serviceRpc = async (name: string, args: Record<string, unknown>) => {
    const { data, error } = await d.service.rpc(name, args);
    failure(error);
    return data;
  };
  const staging = d.service.storage.from("observer-staging");
  /** Trusted storage and the (lazily fetched) result archive of one run. */
  const runResult = (run: string, path: string | null) => {
    const github = !!path?.startsWith("github:");
    const sign = async (name: string) => {
      const { data, error } = await staging.createSignedUrl(name, 120, { download: "observer-result.zip" });
      return error || !data ? null : data.signedUrl;
    };
    // The GitHub URL is looked up only when no stored copy exists yet; a copy is
    // keyed by run + result reference + agent.log, so repeat downloads (and the
    // site's "download all results") never re-fetch from GitHub.
    let githubUrl: Promise<string> | undefined;
    const githubLink = () => {
      if (!d.artifactDownload) throw new ProxyError(503, "artifact_service_unavailable");
      return githubUrl ??= d.artifactDownload(path!);
    };
    let source: Promise<Uint8Array> | undefined;
    const result = () =>
      source ??= (async () => {
        if (!path) throw new Error("result_not_ready");
        if (github) return await fetchArchive(await githubLink(), d.fetchArchive);
        const stored = await staging.download(path);
        if (stored.error || !stored.data) throw new Error("result_download_failed");
        return new Uint8Array(await stored.data.arrayBuffer());
      })();
    const storage: ResultStorage = {
      download: async (name) => {
        const { data, error } = await staging.download(name);
        return error || !data ? null : new Uint8Array(await data.arrayBuffer());
      },
      upload: async (name, bytes) => {
        const { error } = await staging.upload(name, bytes, { contentType: "application/zip", upsert: true });
        if (error) throw new Error("result_upload_failed");
      },
      sign,
    };
    // A GitHub result is read once and kept as the same plain copy download_result
    // serves, so reading the log again does not re-fetch from GitHub.
    const archive = async () => {
      if (!github) return await result();
      const copy = await resultCopyPath(run, path!, null);
      const cached = await storage.download(copy);
      if (cached) return cached;
      const bytes = await result();
      if (bytes.length <= RESULT_ARCHIVE_BYTES) await storage.upload(copy, bytes).catch(() => {});
      return bytes;
    };
    return { storage, result, githubLink, archive };
  };
  switch (body.action) {
    // Relay mode only: the open page answers its team's model calls with a key
    // that exists only in that page. The routes are empty in stored mode.
    case "model_routes":
      return await userRpc("observer_personal_model_routes");
    case "personal_model":
      return await fulfillPersonalModel(body, d.userId, {
        rpc: serviceRpc,
        fetch,
        trustedBases: new Set(d.modelBases),
        resolve: d.resolve,
        send: (topic, event, payload) => sendModelBroadcast(d.service, topic, event, payload),
      });
    case "diagnostics":
      return await userRpc("observer_diagnostics", {
        p_revision: body.revision_id ? uuid(body.revision_id) : null,
        p_run: body.run_id ? uuid(body.run_id) : null,
      });
    case "local_access": {
      const run = uuid(body.run_id);
      const access = await serviceRpc("observer_local_access", { p_run: run, p_user: d.userId });
      if (!access) throw new ProxyError(409, "local_session_not_ready");
      const token = await decryptCredential(access.encrypted_credential, run + ":local", d.masterKey);
      if (!new RegExp("^obs_" + run + "\\.[A-Za-z0-9_-]{40,100}$").test(token)) {
        throw new ProxyError(503, "invalid_local_session");
      }
      return { run_id: run, credential: token, expires_at: access.expires_at };
    }
    case "list": {
      // The caller's own team only: an organizer account may read every team's rows,
      // but its workspace (site and CLI) must not show or resolve another team's versions.
      const profile = await d.service.from("profiles").select("team_id").eq("id", d.userId).maybeSingle();
      failure(profile.error);
      const team = (profile.data as { team_id?: string | null } | null)?.team_id ??
        "00000000-0000-0000-0000-000000000000";
      const queries = [
        d.user.from("observer_phase_settings").select("*,phases(id,slug,name_en,name_zh,starts_at,ends_at,is_active)"),
        d.user.from("observer_projects").select("*,observer_revisions(*,observer_evidence(*))").eq("team_id", team)
          .order("created_at", {
            ascending: false,
          }).limit(100),
        d.user.from("observer_batches").select("*,observer_runs(*)").eq("team_id", team).eq("purpose", "formal").order(
          "created_at",
          {
            ascending: false,
          },
        ).limit(
          50,
        ),
      ];
      const results = await Promise.all(queries);
      results.forEach((r) => failure(r.error));
      // Remaining evaluations are informational; the database enforces the limit.
      const quota = await d.user.rpc("observer_evaluation_quota");
      const finals = await d.user.rpc("observer_final_versions");
      return {
        phases: results[0].data,
        projects: results[1].data,
        batches: results[2].data,
        quota: quota.error ? null : quota.data,
        // The team's own legacy providers only: shared organizer entries (the retired
        // model relay) are not the team's to see.
        providers: ((await userRpc("observer_list_providers")) ?? []).filter((p: { shared?: boolean }) => !p.shared),
        team_model: await userRpc("observer_team_model"),
        team_environment: await optionalUserRpc("observer_team_environment"),
        // Only https:// bases are offered to teams (the page lists no other); internal
        // http:// organizer relays stay private.
        model_bases: d.modelBases.filter((base) => base.startsWith("https://")),
        // Informational like the quota; an older database without the RPC shows no choice.
        final_versions: finals.error ? null : finals.data,
      };
    }
    case "upload": {
      const id = crypto.randomUUID();
      const path = await serviceRpc("observer_reserve_upload", { p_user: d.userId, p_id: id, p_purpose: body.purpose });
      const { data, error } = await staging.createSignedUploadUrl(path, { upsert: false });
      failure(error);
      return { id, path, token: data!.token };
    }
    case "submit_repository": {
      // Accepted: https://github.com/OWNER/REPO, .../tree/<branch, tag or commit>[/<folder>],
      // .../commit/<sha>, plus the optional fields "branch" and "subdir".
      let url: string, parsed: ReturnType<typeof parseSourceUrl>, ref: string | null, subdir: string | null;
      try {
        url = text(body.url, 1024);
        parsed = parseSourceUrl(url);
        ref = sourceRef(body.branch);
        subdir = sourceSubdir(body.subdir);
      } catch (error) {
        const code = error instanceof GitHubError ? error.code : "";
        throw new ProxyError(
          400,
          ["invalid_source_ref", "invalid_source_subdir"].includes(code) ? code : "invalid_repository_url",
        );
      }
      const location = "https://github.com/" + parsed.repository;
      const title = text(body.title, 100);
      // Every submitted version is pinned and preserved before anything else happens:
      // the exact commit is resolved now and its zipball stored (only the chosen folder,
      // under the archive's top folder, when one was named), whether or not the
      // preparation later succeeds and whatever the team does to the repository.
      // Preparation then works on exactly this stored snapshot.
      let snapshot:
        | { commit: string; path: string; bytes: number; sha256: string; ref: string | null; subdir: string | null }
        | null = null;
      if (await serviceRpc("observer_source_snapshots_enabled", {})) {
        if (!d.resolveSource) throw new ProxyError(503, "source_snapshot_unavailable");
        let resolved: { commit: string; archiveUrl: string; ref?: string | null; subdir?: string | null };
        try {
          resolved = await d.resolveSource(url, { ref, subdir });
        } catch (error) {
          const code = (error as { code?: string })?.code ?? "";
          if (code === "github_not_found") throw new ProxyError(400, "repository_not_found");
          if (
            [
              "private_source_requires_zip",
              "source_ref_not_found",
              "source_options_conflict",
              "invalid_source_ref",
              "invalid_source_subdir",
              "invalid_repository_url",
            ].includes(code)
          ) throw new ProxyError(400, code);
          throw new ProxyError(503, "source_snapshot_unavailable");
        }
        if (!/^[0-9a-f]{40}$/.test(resolved.commit)) throw new ProxyError(503, "source_snapshot_unavailable");
        let bytes: Uint8Array<ArrayBuffer>;
        try {
          bytes = await boundedDownload(resolved.archiveUrl, d.fetchArchive ?? fetch, SOURCE_SNAPSHOT_LIMIT);
        } catch (error) {
          throw error instanceof ProxyError ? error : new ProxyError(503, "source_snapshot_unavailable");
        }
        const folder = resolved.subdir ?? null;
        if (folder) bytes = await folderSnapshot(bytes, folder);
        const path = d.userId + "/" + resolved.commit + "-" + crypto.randomUUID() + ".zip";
        const { error } = await d.service.storage.from("observer-sources").upload(path, bytes, {
          contentType: "application/zip",
          upsert: false,
        });
        if (error) throw new ProxyError(503, "source_snapshot_unavailable");
        snapshot = {
          commit: resolved.commit,
          path,
          bytes: bytes.length,
          sha256: await sha256Hex(bytes),
          ref: resolved.ref ?? null,
          subdir: folder,
        };
      } else if (parsed.tree || parsed.commit || ref || subdir) {
        // A branch, commit or folder can only be honoured with a stored snapshot.
        throw new ProxyError(400, "source_options_unavailable");
      }
      const revision = await userRpc("observer_create_project", {
        p_title: title,
        p_source_kind: "repository",
        p_source_location: location,
      });
      if (snapshot) {
        await serviceRpc("observer_record_source_snapshot", {
          p_revision: revision,
          p_commit: snapshot.commit,
          p_path: snapshot.path,
          p_bytes: snapshot.bytes,
          p_sha256: snapshot.sha256,
          ...(snapshot.ref || snapshot.subdir ? { p_ref: snapshot.ref, p_subdir: snapshot.subdir } : {}),
        });
      }
      return snapshot
        ? {
          revision_id: revision,
          source_commit: snapshot.commit,
          ...(snapshot.ref ? { source_ref: snapshot.ref } : {}),
          ...(snapshot.subdir ? { source_subdir: snapshot.subdir } : {}),
        }
        : { revision_id: revision };
    }
    case "submit_zip": {
      const id = uuid(body.upload_id);
      const path = await serviceRpc("observer_upload_access", { p_user: d.userId, p_id: id, p_purpose: "source" });
      if (!path) throw new ProxyError(404, "upload_not_found");
      // Source validation is done by the preparation job. Check completion here
      // without exposing storage credentials or accepting a browser-supplied hash.
      const { data: objects, error } = await staging.list(path.slice(0, path.lastIndexOf("/")), {
        search: "source.zip",
        limit: 2,
      });
      failure(error);
      if (
        !objects?.some((o) =>
          o.name === "source.zip" && Number(o.metadata?.size) > 0 && Number(o.metadata?.size) <= 52428800
        )
      ) {
        throw new ProxyError(400, "upload_not_finished");
      }
      // A ZIP without any program is refused before a revision exists, so it never
      // uses a preparation or a model call (the browser checks the same first).
      let check;
      try {
        const { data: blob, error: readError } = await staging.download(path);
        if (readError || !blob) throw new Error("unreadable");
        check = inspectSourceNames(zipNames(new Uint8Array(await blob.arrayBuffer())));
      } catch {
        check = null; // unreadable here: the preparation job validates the archive as before
      }
      if (check && !check.manifest && !check.code) {
        throw new ProxyError(400, "zip_has_no_code", { files: check.files });
      }
      const revision = await userRpc("observer_submit_zip", { p_title: text(body.title, 100), p_upload: id });
      return check && !check.manifest ? { revision_id: revision, warning: "no_manifest" } : { revision_id: revision };
    }
    case "approve":
      await userRpc("observer_approve_revision", {
        p_revision: uuid(body.revision_id),
        p_digest: text(body.digest, 64),
      });
      return { accepted: true };
    case "evaluate":
      return {
        batch_id: await userRpc("observer_create_batch", {
          p_phase: uuid(body.phase_id),
          p_revision: body.revision_id ? uuid(body.revision_id) : null,
          // Another evaluation of an already evaluated version is an explicit choice.
          ...(body.confirm_repeat === true ? { p_confirm_repeat: true } : {}),
          // 本次不提供模型: the run gets none of the team's model variables (OBSERVER_MODEL_DISABLED=1).
          ...(body.no_model === true ? { p_no_model: true } : {}),
        }),
      };
    // The team's final version for an open formal phase; revision_id null clears
    // the choice. The database checks membership, the version and the deadline.
    case "set_final_version":
      return {
        final_version: await userRpc("observer_set_final_version", {
          p_phase: uuid(body.phase_id),
          p_revision: body.revision_id == null ? null : uuid(body.revision_id),
        }),
      };
    case "withdraw":
      await userRpc("observer_withdraw_revision", { p_revision: uuid(body.revision_id) });
      return { accepted: true };
    case "evidence": {
      const url = text(body.code_url, 1000, true);
      if (url) {
        let parsed: URL;
        try {
          parsed = new URL(url);
        } catch {
          throw new ProxyError(400, "invalid_code_url");
        }
        if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
          throw new ProxyError(400, "invalid_code_url");
        }
      }
      await userRpc("observer_save_evidence", {
        p_revision: uuid(body.revision_id),
        p_notes: text(body.notes, 8000, true),
        p_code_url: url,
      });
      return { accepted: true };
    }
    case "team_environment":
      return { team_environment: await userRpc("observer_team_environment") };
    case "save_team_variable": {
      const name = typeof body.name === "string" ? body.name.trim() : "";
      const secret = body.secret !== false;
      let value = typeof body.value === "string" ? body.value.trim() : "";
      body.value = "";
      if (
        !validTeamVariableName(name) || !value || value.includes("\0") ||
        new TextEncoder().encode(value).length > 8192
      ) throw new ProxyError(400, "invalid_team_variable");
      const id = crypto.randomUUID();
      // Secret values are encrypted here, bound to the variable id; the database only receives ciphertext.
      const encrypted = secret ? await encryptCredential(value, id, d.masterKey) : null;
      const hint = secret ? keyHint(value) : "";
      const plain = secret ? null : value;
      value = "";
      await serviceRpc("observer_save_team_variable", {
        p_user: d.userId,
        p_id: id,
        p_name: name,
        p_secret: secret,
        p_encrypted: encrypted,
        p_plain: plain,
        p_hint: hint,
        // The "model" tag (「添加模型服务」 sends true); absent: kept, or by name for a new variable.
        ...(typeof body.model === "boolean" ? { p_model: body.model } : {}),
      });
      return { team_environment: await userRpc("observer_team_environment") };
    }
    case "delete_team_variable": {
      const name = typeof body.name === "string" ? body.name : "";
      if (!/^[A-Z][A-Z0-9_]{0,63}$/.test(name)) throw new ProxyError(400, "invalid_team_variable");
      await userRpc("observer_delete_team_variable", { p_name: name });
      return { team_environment: await userRpc("observer_team_environment") };
    }
    case "set_team_variable_flags": {
      // Tag a variable as model-related, or switch it off/on without deleting it.
      const name = typeof body.name === "string" ? body.name : "";
      if (
        !/^[A-Z][A-Z0-9_]{0,63}$/.test(name) ||
        (body.model !== undefined && typeof body.model !== "boolean") ||
        (body.disabled !== undefined && typeof body.disabled !== "boolean") ||
        (body.model === undefined && body.disabled === undefined)
      ) throw new ProxyError(400, "invalid_team_variable");
      return {
        team_environment: await userRpc("observer_set_team_variable_flags", {
          p_name: name,
          p_model: body.model ?? null,
          p_disabled: body.disabled ?? null,
        }),
      };
    }
    case "set_team_domains": {
      if (!Array.isArray(body.domains) || body.domains.length > 10) throw new ProxyError(400, "invalid_team_domains");
      const hosts = [
        ...new Set(
          body.domains.map((h: unknown) => typeof h === "string" ? h.trim().toLowerCase().replace(/\.$/, "") : ""),
        ),
      ] as string[];
      if (!hosts.every(validTeamDomain)) throw new ProxyError(400, "invalid_team_domains");
      // Every name must resolve to public addresses now; the sidecar checks again at each connection.
      for (const host of hosts) {
        if (!await publicBase("https://" + host, new Set(), d.resolve)) {
          throw new ProxyError(400, "team_domain_not_public");
        }
      }
      await serviceRpc("observer_set_team_domains", { p_user: d.userId, p_hosts: hosts });
      return { team_environment: await userRpc("observer_team_environment") };
    }
    case "set_team_egress_route": {
      // 出网线路: labels only (direct, cn, overseas); no node detail ever reaches a page.
      if (!["direct", "cn", "overseas"].includes(body.route)) throw new ProxyError(400, "invalid_egress_route");
      if (body.auto_fallback !== undefined && typeof body.auto_fallback !== "boolean") {
        throw new ProxyError(400, "invalid_egress_route");
      }
      return {
        team_environment: await userRpc("observer_set_team_egress_route", {
          p_route: body.route,
          p_auto_fallback: body.auto_fallback ?? null,
        }),
      };
    }
    case "save_provider":
      // The former multi-provider settings stay retired; teams use save_team_model.
      throw new ProxyError(410, "ephemeral_credentials_required");
    case "save_team_model": {
      let key = typeof body.key === "string" ? body.key.trim() : "";
      body.key = "";
      // Any public HTTPS base (or an exact organizer-configured one); never HTTP, an IP or an internal name.
      const base = await publicBase(body.base_url, new Set(d.modelBases), d.resolve);
      if (!base) throw new ProxyError(400, "model_destination_not_enabled");
      const model = typeof body.model === "string" ? body.model.trim() : "";
      if (
        !model || model.length > 256 || /\p{Cc}/u.test(model) ||
        key.length < 8 || key.length > 8192 || /[\s\p{Cc}]/u.test(key)
      ) throw new ProxyError(400, "invalid_team_model");
      if (body.protocol !== undefined && body.protocol !== "openai" && body.protocol !== "anthropic") {
        throw new ProxyError(400, "invalid_team_model");
      }
      const id = crypto.randomUUID();
      // Encrypted here, bound to its provider ID; the database only receives ciphertext.
      const encrypted = await encryptCredential(key, id, d.masterKey);
      const hint = keyHint(key);
      key = "";
      await serviceRpc("observer_save_team_model", {
        p_user: d.userId,
        p_provider: id,
        p_base: base,
        p_model: model,
        p_encrypted_key: encrypted,
        p_key_hint: hint,
        p_protocol: body.protocol ?? "openai",
      });
      return { team_model: await userRpc("observer_team_model") };
    }
    case "delete_team_model":
      return { deleted: await userRpc("observer_delete_team_model") };
    case "set_team_model_mode":
      if (body.mode !== "stored" && body.mode !== "relay") throw new ProxyError(400, "invalid_team_model_mode");
      if (body.protocol !== undefined && body.protocol !== "openai" && body.protocol !== "anthropic") {
        throw new ProxyError(400, "invalid_team_model_mode");
      }
      // Choosing "relay" deletes any saved key in the same database transaction.
      return {
        mode: await userRpc("observer_set_team_model_mode", { p_mode: body.mode, p_protocol: body.protocol ?? null }),
      };
    case "disable_provider":
      await userRpc("observer_disable_provider", { p_id: uuid(body.id) });
      return { accepted: true };
    case "download_project": {
      const reference = await serviceRpc("observer_materialized_project", {
        p_revision: uuid(body.revision_id),
        p_user: d.userId,
      });
      if (!reference) throw new ProxyError(404, "project_not_ready");
      if (!d.artifactDownload) throw new ProxyError(503, "artifact_service_unavailable");
      return { url: await d.artifactDownload(reference) };
    }
    case "agent_log": {
      // The team's own agent.log for one run (one card), for reading on the page.
      // Same visibility as download_result: RLS on the participant's token.
      const run = uuid(body.run_id);
      const { data, error } = await d.user.from("observer_runs").select("result_path").eq("id", run)
        .maybeSingle();
      failure(error);
      if (!data) throw new ProxyError(404, "run_not_found");
      const { storage, archive } = runResult(run, data.result_path ?? null);
      // An executor run keeps its log beside the result; a colocated run has it inside.
      let log = await storedAgentLog(run, storage).catch(() => null);
      if (!log && data.result_path) {
        try {
          log = await resultAgentLog(await archive());
        } catch {
          console.warn("observer-portal: agent.log could not be read from the result");
        }
      }
      return agentLogView(log, body.full === true);
    }
    case "download_result": {
      const run = uuid(body.run_id);
      // Read with the participant's token: RLS hides other teams' runs and runs of a
      // sealed (hidden final) phase until its results are published.
      const { data, error } = await d.user.from("observer_runs").select("result_path").eq("id", run)
        .maybeSingle();
      failure(error);
      if (!data?.result_path) throw new ProxyError(404, "result_not_ready");
      const path: string = data.result_path;
      const github = path.startsWith("github:");
      if (github && !d.artifactDownload) throw new ProxyError(503, "artifact_service_unavailable");
      const { storage, result, githubLink } = runResult(run, path);
      const log = await storedAgentLog(run, storage).catch(() => null);
      // The log is a convenience: without it the same result is served. codeload.github.com
      // only allows cross-origin reads from GitHub's own origins, so a GitHub result is
      // served from our storage, whose signed URLs allow CORS.
      if (log) {
        try {
          return { url: await resultCopy(run, path, log, result, storage) };
        } catch {
          console.warn("observer-portal: agent.log could not be added to the result download");
        }
      }
      if (github) {
        try {
          return { url: await resultCopy(run, path, null, result, storage) };
        } catch {
          console.warn("observer-portal: result could not be copied to storage; serving the GitHub URL");
        }
        return { url: await githubLink() };
      }
      const signed = await staging.createSignedUrl(path, 120, { download: "observer-result.zip" });
      failure(signed.error);
      return { url: signed.data!.signedUrl };
    }
    case "accept_csv": {
      const run = uuid(body.run_id);
      const path = await serviceRpc("observer_upload_access", {
        p_user: d.userId,
        p_id: uuid(body.upload_id),
        p_purpose: "csv",
      });
      if (!path) throw new ProxyError(404, "upload_not_found");
      // Authorization before accessing the file; only the matching team's run.
      const visible = await d.user.from("observer_runs").select("id").eq("id", run).maybeSingle();
      failure(visible.error);
      if (!visible.data) throw new ProxyError(404, "run_not_found");
      const { data, error } = await staging.download(path);
      failure(error);
      if (!data || data.size > 20 * 1024 * 1024) throw new ProxyError(400, "csv_too_large");
      const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", await data.arrayBuffer()));
      const digest = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
      await serviceRpc("observer_accept_uploaded_csv", {
        p_run: run,
        p_user: d.userId,
        p_upload: uuid(body.upload_id),
        p_digest: digest,
      });
      return { accepted: true };
    }
    default:
      throw new ProxyError(400, "unknown_portal_action");
  }
}
