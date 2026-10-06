import { createClient } from "npm:@supabase/supabase-js@2";
import { jobRequest, parseEgressRoutes } from "../_shared/observer-job.ts";
import { ProxyError } from "../_shared/observer-model.ts";
import { GitHubError } from "../_shared/observer-github.ts";
import { artifactDownload, configuredApp } from "../_shared/observer-app.ts";
import { agentLogPath } from "../_shared/observer-agent-log.ts";
import { singleFileZip } from "../_shared/observer-zip.ts";
import { decodeKey } from "../_shared/observer-seal.ts";
import type { EgressRoutes, PublicPoolDependencies } from "../_shared/observer-job.ts";

const service = createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "", {
  auth: { persistSession: false },
});
const staging = () => service.storage.from("observer-staging");
const ARCHIVE_LIMIT = 50 * 1024 * 1024;

async function bounded(response: Response, limit: number) {
  if (!response.ok || !response.body) {
    await response.body?.cancel();
    throw new ProxyError(503, "sealed_input_unavailable");
  }
  const reader = response.body.getReader();
  const parts: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > limit) {
      await reader.cancel();
      throw new ProxyError(503, "sealed_input_too_large");
    }
    parts.push(value);
  }
  const out = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.byteLength;
  }
  return out;
}

async function stored(bucket: string, path: string) {
  const { data, error } = await service.storage.from(bucket).download(path);
  if (error || !data) throw new ProxyError(503, "sealed_input_unavailable");
  if (data.size > ARCHIVE_LIMIT + 1024) throw new ProxyError(503, "sealed_input_too_large");
  return new Uint8Array(await data.arrayBuffer());
}

// Public runner pool (ops/public-runner-pool.md): only when the result sealing
// key is configured; without it public-pool jobs are refused.
function publicPool(): PublicPoolDependencies | undefined {
  const secret = Deno.env.get("OBSERVER_RESULT_SEAL_KEY");
  if (!secret) return undefined;
  let resultKey: Uint8Array;
  try {
    resultKey = decodeKey(secret);
  } catch {
    // A malformed key disables the public pool only, never the private pool.
    console.error("observer-job: OBSERVER_RESULT_SEAL_KEY is not 32 bytes of base64url; public pool disabled");
    return undefined;
  }
  return {
    resultKey,
    readScenario: (path) => stored("observer-scenarios", path),
    readArchive: async (reference) => {
      const url = await artifactDownload(await configuredApp(service), reference, true);
      return await bounded(
        await fetch(url, { redirect: "error", signal: AbortSignal.timeout(60000) }).catch(() => {
          throw new ProxyError(503, "sealed_input_unavailable");
        }),
        ARCHIVE_LIMIT,
      );
    },
    stage: async (job, name, sealed) => {
      const path = "sealed/" + job + "/" + name + ".zip";
      const { error } = await staging().upload(path, sealed, { contentType: "application/zip", upsert: true });
      if (error) throw new ProxyError(503, "sealed_input_unavailable");
      const signed = await staging().createSignedUrl(path, 600);
      if (signed.error || !signed.data?.signedUrl) throw new ProxyError(503, "sealed_input_unavailable");
      return signed.data.signedUrl;
    },
    resultUpload: async (job) => {
      const path = "sealed/" + job + "/result.zip";
      const { data, error } = await staging().createSignedUploadUrl(path, { upsert: true });
      if (error || !data?.signedUrl) throw new ProxyError(503, "sealed_result_unavailable");
      return { url: data.signedUrl, path };
    },
    readResult: (job) => stored("observer-staging", "sealed/" + job + "/result.zip"),
    commitResult: async (user, run, files) => (await configuredApp(service)).commitResult(user, run, files),
  };
}

// Egress route nodes (ops/egress-routes.md). Missing or malformed: no node, so runs on a
// route fall back to direct (or refuse) as each team chose; nothing about them is logged.
function egressRoutes(): EgressRoutes | undefined {
  const secret = Deno.env.get("OBSERVER_EGRESS_ROUTES");
  if (!secret) return undefined;
  try {
    return parseEgressRoutes(secret);
  } catch {
    console.error("observer-job: OBSERVER_EGRESS_ROUTES is malformed; egress routes have no nodes");
    return undefined;
  }
}
const routes = egressRoutes();

const known = new Set([
  "job_unavailable",
  "job_identity_mismatch",
  "job_already_claimed",
  "invalid_job_result",
  "job_result_conflict",
  "job_not_claimed",
  "artifact_access_denied",
]);

Deno.serve({ port: Number(Deno.env.get("OBSERVER_LISTEN_PORT") ?? 8000) }, async (request) => {
  try {
    if (request.method !== "POST") throw new ProxyError(405, "method_not_allowed");
    const data = await jobRequest(request, {
      masterKey: Deno.env.get("OBSERVER_KEY_ENCRYPTION_KEY") ?? "",
      repositoryCredentials: async (user, kind) => {
        const app = await configuredApp(service);
        const repo = await app.privateParticipantRepository(user);
        return { full_name: repo.full_name, token: await app.snapshotWriteToken(user, kind === "prepare") };
      },
      archiveDownload: async (reference, privateOnly) =>
        artifactDownload(await configuredApp(service), reference, privateOnly),
      scenarioDownload: async (path) => {
        const { data, error } = await service.storage.from("observer-scenarios").createSignedUrl(path, 600);
        if (error || !data?.signedUrl) throw new ProxyError(503, "scenario_download_unavailable");
        return data.signedUrl;
      },
      sourceDownload: async (path, bucket = "observer-staging") => {
        const { data, error } = await service.storage.from(bucket).createSignedUrl(path, 600);
        if (error || !data?.signedUrl) throw new ProxyError(503, "source_download_unavailable");
        return data.signedUrl;
      },
      publicPool: publicPool(),
      egressRoutes: routes,
      storeAgentLog: async (run, log) => {
        // The staging bucket only accepts ZIP and CSV objects.
        const { error } = await service.storage.from("observer-staging").upload(
          agentLogPath(run),
          await singleFileZip("agent.log", log),
          { contentType: "application/zip", upsert: true },
        );
        if (error) throw new ProxyError(503, "agent_log_unavailable");
      },
      rpc: async (name, args) => {
        const { data, error } = await service.rpc(name, args);
        if (error) {
          throw new ProxyError(
            known.has(error.message) ? 409 : 503,
            known.has(error.message) ? error.message : "job_service_unavailable",
          );
        }
        return data;
      },
    });
    return Response.json({ data }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    const code = error instanceof ProxyError || error instanceof GitHubError ? error.code : "job_service_unavailable";
    const status = error instanceof ProxyError || error instanceof GitHubError ? error.status || 503 : 503;
    return Response.json({ error: code }, { status, headers: { "cache-control": "no-store" } });
  }
});
