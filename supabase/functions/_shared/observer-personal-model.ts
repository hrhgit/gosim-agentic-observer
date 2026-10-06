/** Personal credentials are never sent to database RPCs or Broadcast. */
import {
  anthropicMessagesUrl,
  boundedJson,
  capability,
  providerError,
  ProxyError,
  type Rpc,
  sendWithModelFallback,
  timedOut,
  validateChat,
  validateMessages,
} from "./observer-model.ts";
import { publicBase, type Resolver } from "./observer-public-base.ts";
export const MAX_PERSONAL_RESPONSE = 192 * 1024;
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/;
export async function personalDigest(body: unknown) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(body)));
  return Array.from(new Uint8Array(bytes), (v) => v.toString(16).padStart(2, "0")).join("");
}
type PersonalExchangeDeps = {
  rpc: Rpc;
  exchange: (topic: string, call: string, payload: Record<string, unknown>) => Promise<unknown>;
};
async function personalCall(
  request: Request,
  topic: string,
  protocol: "openai" | "anthropic",
  validate: (body: unknown) => { body: Record<string, unknown> },
  deps: PersonalExchangeDeps,
) {
  const { run, token } = capability(request.headers.get("authorization"), request.headers.get("x-api-key"));
  const body = validate(await boundedJson(request, 65536)).body;
  const call = request.headers.get("idempotency-key") ?? crypto.randomUUID();
  if (!UUID.test(call)) throw new ProxyError(400, "invalid_idempotency_key");
  if (
    !await deps.rpc("observer_request_personal_model", {
      p_run: run,
      p_token: token,
      p_call: call,
      p_digest: await personalDigest(body),
    })
  ) throw new ProxyError(409, "model_request_already_received");
  try {
    const payload: Record<string, unknown> = { run_id: run, call_id: call, body, protocol };
    if (protocol === "anthropic") {
      payload.anthropic_version = request.headers.get("anthropic-version") ?? "2023-06-01";
      const beta = request.headers.get("anthropic-beta");
      if (beta) payload.anthropic_beta = beta;
    }
    const result = await deps.exchange(topic, call, payload);
    const list = protocol === "anthropic" ? (result as any)?.content : (result as any)?.choices;
    if (!result || typeof result !== "object" || !Array.isArray(list)) {
      throw new ProxyError(502, "invalid_provider_response");
    }
    const text = JSON.stringify(result);
    if (new TextEncoder().encode(text).length > MAX_PERSONAL_RESPONSE) {
      throw new ProxyError(502, "model_response_too_large");
    }
    return new Response(text, {
      headers: { "content-type": "application/json", "cache-control": "no-store", "x-observer-request-id": call },
    });
  } finally {
    await deps.rpc("observer_finish_personal_model", { p_call: call, p_status: "timeout" });
  }
}
export function personalChat(request: Request, topic: string, deps: PersonalExchangeDeps) {
  return personalCall(request, topic, "openai", validateChat, deps);
}
/** Relay mode, Anthropic Messages shape: the open page answers with its own key. */
export function personalMessages(request: Request, topic: string, deps: PersonalExchangeDeps) {
  return personalCall(request, topic, "anthropic", validateMessages, deps);
}
function redact(value: any, key: string): any {
  if (typeof value === "string") return value.replaceAll(key, "[REDACTED]");
  if (Array.isArray(value)) return value.map((item) => redact(item, key));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k.replaceAll(key, "[REDACTED]"), redact(v, key)]));
  }
  return value;
}
export async function fulfillPersonalModel(
  input: any,
  user: string,
  deps: {
    rpc: Rpc;
    fetch: typeof fetch;
    // Exact organizer-configured bases, trusted as they are; any other base must be public HTTPS.
    trustedBases: Set<string>;
    resolve?: Resolver | null;
    send: (topic: string, event: string, payload: unknown) => Promise<void>;
  },
) {
  if (
    !input || !UUID.test(input.run_id ?? "") || !UUID.test(input.call_id ?? "") || typeof input.api_key !== "string" ||
    input.api_key.length < 1 || input.api_key.length > 8192 || /[\r\n]/.test(input.api_key) ||
    typeof input.model !== "string" || input.model.length < 1 || input.model.length > 256 ||
    // deno-lint-ignore no-control-regex
    /[\u0000-\u001f\u007f-\u009f]/.test(input.model)
  ) throw new ProxyError(400, "invalid_personal_model");
  const protocol = input.protocol === "anthropic" ? "anthropic" : "openai";
  const body = (protocol === "anthropic" ? validateMessages(input.body) : validateChat(input.body)).body;
  const normalized = await publicBase(input.base_url, deps.trustedBases, deps.resolve);
  if (!normalized) throw new ProxyError(400, "provider_not_authorized");
  // Claim before billing: team, run and exact prompt digest must match.
  const topic = await deps.rpc("observer_claim_personal_model", {
    p_user: user,
    p_run: input.run_id,
    p_call: input.call_id,
    p_digest: await personalDigest(body),
  });
  let message: Record<string, unknown> = { call_id: input.call_id, error: "personal_model_failed" }, status = "failed";
  try {
    const url = protocol === "anthropic" ? anthropicMessagesUrl(normalized) : normalized + "/chat/completions";
    const headers: Record<string, string> = protocol === "anthropic"
      ? {
        "content-type": "application/json",
        "x-api-key": input.api_key,
        "anthropic-version": typeof input.anthropic_version === "string" ? input.anthropic_version : "2023-06-01",
        ...(typeof input.anthropic_beta === "string" ? { "anthropic-beta": input.anthropic_beta } : {}),
      }
      : { "content-type": "application/json", authorization: "Bearer " + input.api_key };
    const signal = AbortSignal.timeout(110000);
    // The agent's model (already in the digest-checked body) wins; the page's model is
    // the default, used when the agent sent none or the provider does not know its model.
    const response = await sendWithModelFallback(
      (model) =>
        deps.fetch(url, {
          method: "POST",
          redirect: "error",
          signal,
          headers,
          body: JSON.stringify({ ...body, model }),
        }),
      body.model,
      input.model,
    );
    if (!response.ok) {
      await response.body?.cancel();
      throw providerError(response.status);
    }
    const result = await boundedJson(response, MAX_PERSONAL_RESPONSE);
    const list = protocol === "anthropic" ? (result as any)?.content : (result as any)?.choices;
    if (!result || typeof result !== "object" || !Array.isArray(list)) {
      throw new ProxyError(502, "invalid_provider_response");
    }
    message = { call_id: input.call_id, result: redact(result, input.api_key) };
    status = "done";
  } catch (error) {
    /* Never copy provider errors, headers or credentials; only say it was too slow. */
    if (timedOut(error)) message = { call_id: input.call_id, error: "personal_model_timeout" };
  } finally {
    input.api_key = "";
    await deps.rpc("observer_finish_personal_model", { p_call: input.call_id, p_status: status });
    await deps.send(topic, "response", message);
  }
  return { completed: status === "done" };
}
