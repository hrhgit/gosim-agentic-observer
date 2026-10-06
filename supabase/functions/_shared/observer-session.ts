import { boundedJson, capability, ProxyError } from "./observer-model.ts";
import type { Rpc } from "./observer-model.ts";

// Server-side long polling: runners are far from the database, so each empty
// poll used to cost a full ocean round trip. Waiting here keeps the checks next
// to the database; the response is the same observer_poll result as before.
const MAX_WAIT_SECONDS = 15;
const WAIT_TARGETS = new Set(["ready", "observation", "response"]);
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function waitRequest(body: Record<string, unknown>): { seconds: number; target: string } | null {
  if (body.wait === undefined) return null;
  if (typeof body.wait !== "number" || !Number.isFinite(body.wait) || body.wait < 0) {
    throw new ProxyError(400, "invalid_wait");
  }
  const target = body.wait_for ?? "response";
  if (typeof target !== "string" || !WAIT_TARGETS.has(target)) throw new ProxyError(400, "invalid_wait");
  return { seconds: Math.min(body.wait, MAX_WAIT_SECONDS), target };
}

type StepState = { ready: boolean; observed: boolean; answered: boolean };

export async function waitForStep(
  rpc: Rpc,
  base: Record<string, unknown>,
  scope: string,
  wait: { seconds: number; target: string },
  now: () => number = Date.now,
  pause: (ms: number) => Promise<unknown> = sleep,
): Promise<void> {
  const end = now() + wait.seconds * 1000;
  let interval = 15;
  while (true) {
    const state = await rpc("observer_step_state", { ...base, p_scope: scope }) as StepState;
    const done = wait.target === "ready"
      ? state.ready
      : wait.target === "observation"
      ? state.observed && !state.answered
      : state.answered;
    const left = end - now();
    if (done || left <= 0) return;
    await pause(Math.min(interval, left));
    interval = Math.min(interval * 1.5, 150);
  }
}

// Formal observations are ~0.5 MB of JSON per step and cross the ocean twice;
// gzip makes them ~10x smaller. Both directions are opt-in per request.
const COMPRESS_FROM_BYTES = 16 * 1024;

export function decodedRequest(request: Request): Request {
  const encoding = request.headers.get("content-encoding")?.trim().toLowerCase();
  if (!encoding || encoding === "identity") return request;
  if (encoding !== "gzip" || !request.body) throw new ProxyError(415, "unsupported_content_encoding");
  // boundedJson still caps the decompressed size while streaming.
  return new Request(request.url, {
    method: request.method,
    headers: request.headers,
    body: request.body.pipeThrough(new DecompressionStream("gzip")),
  });
}

export function jsonResponse(value: unknown, acceptEncoding: string | null, headers: Record<string, string>): Response {
  const text = JSON.stringify(value);
  if (text.length < COMPRESS_FROM_BYTES || !/\bgzip\b/i.test(acceptEncoding ?? "")) {
    return new Response(text, { headers });
  }
  const body = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
  return new Response(body, { headers: { ...headers, "content-encoding": "gzip", vary: "accept-encoding" } });
}

export async function sessionRequest(request: Request, rpc: Rpc): Promise<unknown> {
  const { run, token } = capability(request.headers.get("authorization"));
  const body = await boundedJson(request, 17 * 1024 * 1024);
  if (!body || typeof body !== "object" || Array.isArray(body) || typeof body.action !== "string") {
    throw new ProxyError(400, "invalid_session_request");
  }
  const base = { p_run: run, p_token: token };
  // Fixed mapping: a request can never pick an arbitrary privileged RPC.
  switch (body.action) {
    case "abort":
      return await rpc("observer_abort_local", base);
    case "decisions":
      return await rpc("observer_export_decisions", base);
    case "status":
      return await rpc("observer_run_status", base);
    case "poll": {
      if (body.scope !== undefined && body.scope !== "engine" && body.scope !== "participant") {
        throw new ProxyError(400, "invalid_scope");
      }
      const scope = body.scope ?? "participant";
      const wait = waitRequest(body);
      if (wait) await waitForStep(rpc, base, scope, wait);
      return await rpc("observer_poll", { ...base, p_scope: scope, p_initialized: body.initialized === true });
    }
    case "ready":
      return await rpc("observer_ready", base);
    case "respond": {
      // With wait, one request answers this step and returns the next one.
      const wait = waitRequest(body);
      const result = await rpc("observer_respond", { ...base, p_sequence: body.sequence, p_response: body.response });
      if (!wait) return result;
      await waitForStep(rpc, base, "participant", wait);
      return await rpc("observer_poll", { ...base, p_scope: "participant", p_initialized: true });
    }
    case "initialize":
      return await rpc("observer_publish_initial", { ...base, p_publication: body.publication });
    case "record_instance":
      return await rpc("observer_record_instance", { ...base, p_record: body.record });
    case "begin":
      return await rpc("observer_begin", base);
    case "publish":
      return await rpc("observer_publish_step", {
        ...base,
        p_sequence: body.sequence,
        p_observation: body.observation,
      });
    case "advance": {
      // Engine: commit the previous step(s), publish the next observation and
      // wait for the participant's answer, in one request instead of three.
      const commits = body.commits ?? [];
      if (!Array.isArray(commits) || commits.length > 8) throw new ProxyError(400, "invalid_commit");
      for (const item of commits) {
        if (!item || typeof item !== "object" || Array.isArray(item)) throw new ProxyError(400, "invalid_commit");
        await rpc("observer_commit_step", { ...base, p_sequence: item.sequence, p_committed: item.committed });
      }
      await rpc("observer_publish_step", { ...base, p_sequence: body.sequence, p_observation: body.observation });
      const wait = waitRequest(body);
      if (wait) await waitForStep(rpc, base, "engine", wait);
      return await rpc("observer_poll", { ...base, p_scope: "engine" });
    }
    case "commit":
      return await rpc("observer_commit_step", { ...base, p_sequence: body.sequence, p_committed: body.committed });
    case "finish":
      return await rpc("observer_finish_run", {
        ...base,
        p_summary: body.summary,
        p_decisions_digest: body.decisions_digest,
        p_result_path: body.result_path,
      });
    case "fail":
      return await rpc("observer_fail_run", { ...base, p_error: body.error });
    default:
      throw new ProxyError(400, "unknown_session_action");
  }
}
