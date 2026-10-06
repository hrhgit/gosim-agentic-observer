/** Model proxy core. No model credential ever reaches the participant process. */
import { publicBase, type Resolver } from "./observer-public-base.ts";

export class ProxyError extends Error {
  constructor(public status: number, public code: string, public detail?: Record<string, unknown>) {
    super(code);
  }
}

/** The provider's HTTP status only (no body/headers), so teams can tell a bad key from a bad model name. */
export function providerError(status: number): ProxyError {
  return new ProxyError(
    502,
    "model_provider_error",
    Number.isInteger(status) && status >= 100 && status <= 599 ? { provider_status: status } : undefined,
  );
}

/** The provider did not answer before our own deadline (not a network failure). */
export function timedOut(error: unknown): boolean {
  return error instanceof DOMException && error.name === "TimeoutError";
}

export type Rpc = (name: string, args: Record<string, unknown>) => Promise<any>;
export type ProxyDependencies = {
  rpc: Rpc;
  fetch: typeof fetch;
  decrypt: (ciphertext: string, providerId: string) => Promise<string>;
  // Exact backend-configured bases. Participants cannot authorize a new origin.
  allowedBases: Set<string>;
  allowedHttpBases: Set<string>;
  defaultProvider: string;
  timeoutMs?: number;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SCOPED_TOKEN = /^obs_([0-9a-f-]{36})\.([A-Za-z0-9_-]{40,200})$/i;
/**
 * The Anthropic SDK sends its key as a bare `x-api-key` header (no "Bearer "
 * scheme); the OpenAI SDK sends `Authorization: Bearer <key>`. Accept either,
 * matched strictly against its own expected shape.
 */
export function capability(authorization: string | null, apiKey: string | null = null) {
  if (apiKey) {
    const match = SCOPED_TOKEN.exec(apiKey);
    if (match && UUID.test(match[1])) return { run: match[1], token: match[2] };
  }
  const match = /^Bearer /i.test(authorization ?? "") ? SCOPED_TOKEN.exec((authorization ?? "").slice(7)) : null;
  if (!match || !UUID.test(match[1])) throw new ProxyError(401, "invalid_run_credential");
  return { run: match[1], token: match[2] };
}

export async function boundedJson(input: Request | Response, limit: number): Promise<any> {
  if (!input.body) throw new ProxyError(400, "empty_body");
  const reader = input.body.getReader();
  const parts: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > limit) throw new ProxyError(413, "body_too_large");
      parts.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  const data = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    data.set(part, offset);
    offset += part.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(data));
  } catch {
    throw new ProxyError(400, "invalid_json");
  }
}

function object(value: unknown): value is Record<string, any> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// deno-lint-ignore no-control-regex
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;
/** The request's `model` is optional; when present it must be 1-256 characters without control characters. */
function checkModel(model: unknown) {
  if (model === undefined || model === "") return;
  if (typeof model !== "string" || model.length > 256 || CONTROL.test(model)) {
    throw new ProxyError(400, "invalid_model");
  }
}

/** A model name that always means "the team's default model" (also what platform preparation sends). */
export const DEFAULT_MODEL_ALIAS = "team-model";

/**
 * Which model the team's own provider receives. Every team route uses the team's own
 * key, so the agent's model is passed through: a project may use a fast model for some
 * steps and a stronger one for others (the team's key pays). Without a model, with ""
 * or with DEFAULT_MODEL_ALIAS the team's saved default is used. Organizer-credit calls
 * (chatCompletion) keep their own database model list.
 */
export function chooseModel(requested: unknown, configured: string): string {
  if (typeof requested !== "string" || !requested || requested === DEFAULT_MODEL_ALIAS) return configured;
  return requested;
}

const MODEL_ERROR_STATUS = new Set([400, 404, 422]);
const MENTIONS_MODEL = /model|模型/i;
const UNKNOWN =
  /not[\s_-]?found|not[\s_-]?exists?|does ?n[o']t exist|invalid|unknown|unsupported|not supported|not a valid|no such|不存在|无效|不支持|未找到|找不到/i;

/** Reads at most `limit` bytes of a response body as text (truncated, never thrown on size). */
async function boundedText(response: Response, limit: number): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const parts: Uint8Array[] = [];
  let length = 0;
  try {
    while (length < limit) {
      const { value, done } = await reader.read();
      if (done) break;
      parts.push(value);
      length += value.byteLength;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  const data = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    data.set(part, offset);
    offset += part.byteLength;
  }
  return new TextDecoder().decode(data.subarray(0, limit));
}

/** Conservative: a 400/404/422 whose (bounded) body says the model is unknown or unsupported.
 * The body is only inspected here and never forwarded. Always consumes the body. */
export async function unknownModelRejection(response: Response): Promise<boolean> {
  if (!MODEL_ERROR_STATUS.has(response.status)) {
    await response.body?.cancel().catch(() => {});
    return false;
  }
  let text: string;
  try {
    text = await boundedText(response, 16384);
  } catch {
    return false;
  }
  return MENTIONS_MODEL.test(text) && UNKNOWN.test(text);
}

/**
 * Sends the agent's model; when the provider rejects that model name as unknown,
 * retries once with the team's default model (so code that still sends an example
 * model name keeps working). No retry for any other failure, or when the agent's
 * model already was the default. Both attempts belong to one call: one reservation,
 * one digest/claim, one settlement, one shared deadline.
 */
export async function sendWithModelFallback(
  send: (model: string) => Promise<Response>,
  requested: unknown,
  configured: string,
): Promise<Response> {
  const model = chooseModel(requested, configured);
  const first = await send(model);
  if (first.ok || model === configured) return first;
  if (!(await unknownModelRejection(first))) return new Response(null, { status: first.status });
  return await send(configured);
}

export function validateChat(body: unknown) {
  if (!object(body)) throw new ProxyError(400, "invalid_chat");
  const allowed = new Set([
    "model",
    "messages",
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "stop",
    "tools",
    "tool_choice",
    "response_format",
    "seed",
    "stream",
    "n",
    "chat_template_kwargs",
  ]);
  if (Object.keys(body).some((k) => !allowed.has(k))) throw new ProxyError(400, "unsupported_chat_option");
  checkModel(body.model);
  if (!Array.isArray(body.messages) || !body.messages.length || body.messages.length > 128) {
    throw new ProxyError(400, "invalid_chat");
  }
  if (body.stream === true) throw new ProxyError(400, "streaming_not_supported");
  if (body.stream !== undefined && body.stream !== false) throw new ProxyError(400, "invalid_stream");
  if (body.n !== undefined && body.n !== 1) throw new ProxyError(400, "one_completion_required");
  if (body.max_tokens !== undefined && body.max_completion_tokens !== undefined) {
    throw new ProxyError(400, "ambiguous_token_limit");
  }
  const maxTokens = body.max_completion_tokens ?? body.max_tokens ?? 1024;
  if (!Number.isSafeInteger(maxTokens) || maxTokens < 1 || maxTokens > 4096) {
    throw new ProxyError(400, "invalid_token_limit");
  }
  for (const message of body.messages) {
    if (
      !object(message) || !["system", "developer", "user", "assistant", "tool"].includes(message.role) ||
      (typeof message.content !== "string" && !(message.content === null && Array.isArray(message.tool_calls)))
    ) {
      throw new ProxyError(400, "text_messages_required");
    }
    // Remote image/audio/attachment URLs would invalidate bounded token accounting.
    if (Object.keys(message).some((k) => !["role", "content", "name", "tool_calls", "tool_call_id"].includes(k))) {
      throw new ProxyError(400, "unsupported_message_option");
    }
  }
  if (
    body.chat_template_kwargs !== undefined &&
    (!object(body.chat_template_kwargs) ||
      Object.keys(body.chat_template_kwargs).some((k) => k !== "enable_thinking") ||
      typeof body.chat_template_kwargs.enable_thinking !== "boolean")
  ) {
    throw new ProxyError(400, "unsupported_template_option");
  }
  const cleaned: Record<string, any> = { ...body, model: body.model, stream: false, n: 1 };
  if (body.max_completion_tokens === undefined) cleaned.max_tokens = maxTokens;
  const bytes = new TextEncoder().encode(JSON.stringify(cleaned)).length;
  if (bytes > 65536) throw new ProxyError(413, "chat_too_large");
  // Byte count is deliberately conservative for supported text tokenizers. Add
  // room for the provider's role/tool template and bound completion explicitly.
  return { body: cleaned, reservedTokens: bytes + body.messages.length * 128 + 1024 + maxTokens };
}

function anthropicBlock(block: unknown): boolean {
  if (!object(block) || typeof block.type !== "string") return false;
  const keys = Object.keys(block);
  if (block.type === "text") return typeof block.text === "string" && keys.every((k) => k === "type" || k === "text");
  if (block.type === "tool_use") {
    return typeof block.id === "string" && typeof block.name === "string" && object(block.input) &&
      keys.every((k) => ["type", "id", "name", "input"].includes(k));
  }
  if (block.type === "tool_result") {
    const content = block.content;
    const okContent = typeof content === "string" ||
      (Array.isArray(content) &&
        content.every((c) => object(c) && c.type === "text" && typeof c.text === "string"));
    return typeof block.tool_use_id === "string" && okContent &&
      (block.is_error === undefined || typeof block.is_error === "boolean") &&
      keys.every((k) => ["type", "tool_use_id", "content", "is_error"].includes(k));
  }
  // Remote image/document blocks would invalidate bounded token accounting.
  return false;
}

/** Anthropic Messages API: same bounds and philosophy as validateChat, different shape. */
export function validateMessages(body: unknown) {
  if (!object(body)) throw new ProxyError(400, "invalid_chat");
  const allowed = new Set([
    "model",
    "messages",
    "max_tokens",
    "system",
    "temperature",
    "top_p",
    "top_k",
    "stop_sequences",
    "tools",
    "tool_choice",
    "metadata",
    "stream",
  ]);
  if (Object.keys(body).some((k) => !allowed.has(k))) throw new ProxyError(400, "unsupported_chat_option");
  checkModel(body.model);
  if (!Array.isArray(body.messages) || !body.messages.length || body.messages.length > 128) {
    throw new ProxyError(400, "invalid_chat");
  }
  if (body.stream === true) throw new ProxyError(400, "streaming_not_supported");
  if (body.stream !== undefined && body.stream !== false) throw new ProxyError(400, "invalid_stream");
  const maxTokens = body.max_tokens;
  if (!Number.isSafeInteger(maxTokens) || maxTokens < 1 || maxTokens > 4096) {
    throw new ProxyError(400, "invalid_token_limit");
  }
  for (const message of body.messages) {
    if (
      !object(message) || !["user", "assistant"].includes(message.role) ||
      Object.keys(message).some((k) => k !== "role" && k !== "content")
    ) {
      throw new ProxyError(400, "text_messages_required");
    }
    if (
      typeof message.content !== "string" &&
      !(Array.isArray(message.content) && message.content.length && message.content.every(anthropicBlock))
    ) {
      throw new ProxyError(400, "text_messages_required");
    }
  }
  if (
    body.system !== undefined && typeof body.system !== "string" &&
    !(Array.isArray(body.system) &&
      body.system.every((b) =>
        object(b) && b.type === "text" && typeof b.text === "string" &&
        Object.keys(b).every((k) => k === "type" || k === "text")
      ))
  ) {
    throw new ProxyError(400, "unsupported_chat_option");
  }
  const cleaned: Record<string, any> = { ...body, model: body.model, stream: false };
  const bytes = new TextEncoder().encode(JSON.stringify(cleaned)).length;
  if (bytes > 65536) throw new ProxyError(413, "chat_too_large");
  return { body: cleaned, reservedTokens: bytes + body.messages.length * 128 + 1024 + maxTokens };
}

/** The official Anthropic SDK appends "/v1/messages" to its base_url; the team's
 * saved base follows the same /v1-or-not convention as the OpenAI path.
 */
export function anthropicMessagesUrl(base: string): string {
  return base.endsWith("/v1") ? base + "/messages" : base + "/v1/messages";
}

function upstreamUrl(base: string, allowHttp: boolean, deps: ProxyDependencies): URL {
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    throw new ProxyError(503, "provider_configuration_error");
  }
  const normalized = url.href.replace(/\/$/, "");
  if (
    url.username || url.password || url.search || url.hash ||
    !deps.allowedBases.has(normalized) ||
    (url.protocol !== "https:" && !(url.protocol === "http:" && allowHttp && deps.allowedHttpBases.has(normalized)))
  ) {
    throw new ProxyError(503, "provider_not_authorized");
  }
  return new URL(normalized + "/chat/completions");
}

async function digest(value: string) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes), (n) => n.toString(16).padStart(2, "0")).join("");
}

function redact(value: any, key: string): any {
  if (typeof value === "string") return value.replaceAll(key, "[REDACTED]");
  if (Array.isArray(value)) return value.map((item) => redact(item, key));
  if (object(value)) {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k.replaceAll(key, "[REDACTED]"), redact(v, key)]));
  }
  return value;
}

export const MAX_TEAM_RESPONSE = 192 * 1024;
export type TeamProxyDependencies = {
  rpc: Rpc;
  fetch: typeof fetch;
  decrypt: (ciphertext: string, providerId: string) => Promise<string>;
  // Exact organizer-configured bases, trusted as they are; any other base must be public HTTPS.
  trustedBases: Set<string>;
  resolve?: Resolver | null;
  timeoutMs?: number;
};

/** Formal runs: the team's saved API, decrypted only in this request's memory.
 * The database picks the provider; the project cannot select another one, and
 * no organizer provider is ever substituted when none is saved.
 */
export async function teamChatCompletion(request: Request, deps: TeamProxyDependencies): Promise<Response> {
  const { run, token } = capability(request.headers.get("authorization"));
  const checked = validateChat(await boundedJson(request, 65536));
  const requestedId = request.headers.get("idempotency-key");
  if (requestedId && !UUID.test(requestedId)) throw new ProxyError(400, "invalid_idempotency_key");
  const call = requestedId ?? crypto.randomUUID();
  const reservation = await deps.rpc("observer_reserve_team_model", {
    p_run: run,
    p_token: token,
    p_call: call,
    p_digest: await digest("team\n" + JSON.stringify(checked.body)),
    p_tokens: checked.reservedTokens,
  });
  if (!reservation?.reserved) throw new ProxyError(409, "model_request_already_received");
  let actualTokens: number | null = null;
  let upstreamAttempted = false;
  let key = "";
  try {
    const base = await publicBase(reservation.base_url, deps.trustedBases, deps.resolve);
    if (
      !base || typeof reservation.model !== "string" || !reservation.model || reservation.model.length > 256 ||
      typeof reservation.provider_id !== "string" || !UUID.test(reservation.provider_id)
    ) throw new ProxyError(503, "provider_not_authorized");
    key = await deps.decrypt(String(reservation.encrypted_key ?? ""), reservation.provider_id);
    if (!key) throw new ProxyError(503, "provider_configuration_error");
    upstreamAttempted = true;
    const signal = AbortSignal.timeout(deps.timeoutMs ?? 120000);
    const response = await sendWithModelFallback(
      (model) =>
        deps.fetch(base + "/chat/completions", {
          method: "POST",
          redirect: "error",
          headers: { "content-type": "application/json", "authorization": "Bearer " + key },
          body: JSON.stringify({ ...checked.body, model }),
          signal,
        }),
      checked.body.model,
      reservation.model,
    );
    if (!response.ok) {
      await response.body?.cancel();
      // No completion was returned: nothing is charged against the team quota.
      actualTokens = 0;
      throw providerError(response.status);
    }
    let result: unknown;
    try {
      result = await boundedJson(response, MAX_TEAM_RESPONSE);
    } catch (error) {
      if (timedOut(error)) throw error;
      if (error instanceof ProxyError && error.code === "body_too_large") {
        throw new ProxyError(502, "model_response_too_large");
      }
      throw new ProxyError(502, "invalid_provider_response");
    }
    if (!object(result) || !Array.isArray(result.choices)) throw new ProxyError(502, "invalid_provider_response");
    const usage = result.usage?.total_tokens;
    if (Number.isSafeInteger(usage) && usage >= 0 && usage <= checked.reservedTokens) actualTokens = usage;
    return new Response(JSON.stringify(redact(result, key)), {
      status: 200,
      headers: { "content-type": "application/json", "cache-control": "no-store", "x-observer-request-id": call },
    });
  } catch (error) {
    if (!upstreamAttempted) actualTokens = 0;
    // Provider bodies, headers, redirects and exception text never leave here.
    if (error instanceof ProxyError) throw error;
    if (timedOut(error)) throw new ProxyError(504, "model_provider_timeout");
    throw new ProxyError(502, "model_provider_unavailable");
  } finally {
    key = "";
    // A failed settlement leaves the reservation for the conservative reconciler.
    await deps.rpc("observer_settle_model", { p_call: call, p_actual_tokens: actualTokens });
  }
}

/** Formal runs, Anthropic Messages shape: same accounting and safety as
 * teamChatCompletion, routed to <base>/v1/messages with x-api-key auth.
 */
export async function teamMessages(request: Request, deps: TeamProxyDependencies): Promise<Response> {
  const { run, token } = capability(request.headers.get("authorization"), request.headers.get("x-api-key"));
  const checked = validateMessages(await boundedJson(request, 65536));
  const requestedId = request.headers.get("idempotency-key");
  if (requestedId && !UUID.test(requestedId)) throw new ProxyError(400, "invalid_idempotency_key");
  const call = requestedId ?? crypto.randomUUID();
  const reservation = await deps.rpc("observer_reserve_team_model", {
    p_run: run,
    p_token: token,
    p_call: call,
    p_digest: await digest("messages\n" + JSON.stringify(checked.body)),
    p_tokens: checked.reservedTokens,
  });
  if (!reservation?.reserved) throw new ProxyError(409, "model_request_already_received");
  let actualTokens: number | null = null;
  let upstreamAttempted = false;
  let key = "";
  try {
    const base = await publicBase(reservation.base_url, deps.trustedBases, deps.resolve);
    if (
      !base || typeof reservation.model !== "string" || !reservation.model || reservation.model.length > 256 ||
      typeof reservation.provider_id !== "string" || !UUID.test(reservation.provider_id)
    ) throw new ProxyError(503, "provider_not_authorized");
    key = await deps.decrypt(String(reservation.encrypted_key ?? ""), reservation.provider_id);
    if (!key) throw new ProxyError(503, "provider_configuration_error");
    upstreamAttempted = true;
    const signal = AbortSignal.timeout(deps.timeoutMs ?? 120000);
    const response = await sendWithModelFallback(
      (model) =>
        deps.fetch(anthropicMessagesUrl(base), {
          method: "POST",
          redirect: "error",
          headers: {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": request.headers.get("anthropic-version") ?? "2023-06-01",
            ...(request.headers.get("anthropic-beta")
              ? { "anthropic-beta": request.headers.get("anthropic-beta")! }
              : {}),
          },
          body: JSON.stringify({ ...checked.body, model }),
          signal,
        }),
      checked.body.model,
      reservation.model,
    );
    if (!response.ok) {
      await response.body?.cancel();
      // No completion was returned: nothing is charged against the team quota.
      actualTokens = 0;
      throw providerError(response.status);
    }
    let result: unknown;
    try {
      result = await boundedJson(response, MAX_TEAM_RESPONSE);
    } catch (error) {
      if (timedOut(error)) throw error;
      if (error instanceof ProxyError && error.code === "body_too_large") {
        throw new ProxyError(502, "model_response_too_large");
      }
      throw new ProxyError(502, "invalid_provider_response");
    }
    if (!object(result) || !Array.isArray(result.content)) throw new ProxyError(502, "invalid_provider_response");
    const usage = result.usage;
    const input = usage?.input_tokens, output = usage?.output_tokens;
    if (Number.isSafeInteger(input) && input >= 0 && Number.isSafeInteger(output) && output >= 0) {
      const total = input + output;
      if (total <= checked.reservedTokens) actualTokens = total;
    }
    return new Response(JSON.stringify(redact(result, key)), {
      status: 200,
      headers: { "content-type": "application/json", "cache-control": "no-store", "x-observer-request-id": call },
    });
  } catch (error) {
    if (!upstreamAttempted) actualTokens = 0;
    // Provider bodies, headers, redirects and exception text never leave here.
    if (error instanceof ProxyError) throw error;
    if (timedOut(error)) throw new ProxyError(504, "model_provider_timeout");
    throw new ProxyError(502, "model_provider_unavailable");
  } finally {
    key = "";
    // A failed settlement leaves the reservation for the conservative reconciler.
    await deps.rpc("observer_settle_model", { p_call: call, p_actual_tokens: actualTokens });
  }
}

export async function chatCompletion(request: Request, deps: ProxyDependencies): Promise<Response> {
  const { run, token } = capability(request.headers.get("authorization"));
  const checked = validateChat(await boundedJson(request, 65536));
  // Organizer credits: the provider's own model list (database) is the allowlist.
  if (typeof checked.body.model !== "string" || !checked.body.model) throw new ProxyError(400, "invalid_provider");
  const separator = checked.body.model.indexOf("::");
  const provider = separator < 0 ? deps.defaultProvider : checked.body.model.slice(0, separator);
  const model = separator < 0 ? checked.body.model : checked.body.model.slice(separator + 2);
  if (!UUID.test(provider) || !model) throw new ProxyError(400, "invalid_provider");
  const requestedId = request.headers.get("idempotency-key");
  if (requestedId && !UUID.test(requestedId)) throw new ProxyError(400, "invalid_idempotency_key");
  const call = requestedId ?? crypto.randomUUID();
  const body = JSON.stringify({ ...checked.body, model });
  const reservation = await deps.rpc("observer_reserve_model", {
    p_run: run,
    p_token: token,
    p_call: call,
    p_provider: provider,
    p_model: model,
    p_digest: await digest(provider + "\n" + body),
    p_tokens: checked.reservedTokens,
  });
  if (!reservation.reserved) throw new ProxyError(409, "model_request_already_received");
  let actualTokens: number | null = null;
  let upstreamAttempted = false;
  try {
    const url = upstreamUrl(reservation.base_url, reservation.allow_http === true, deps);
    const key = await deps.decrypt(reservation.encrypted_key, provider);
    if (!key) throw new ProxyError(503, "provider_configuration_error");
    upstreamAttempted = true;
    const response = await deps.fetch(url, {
      method: "POST",
      redirect: "error",
      headers: { "content-type": "application/json", "authorization": "Bearer " + key },
      body,
      signal: AbortSignal.timeout(deps.timeoutMs ?? 120000),
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw providerError(response.status);
    }
    const result = await boundedJson(response, 2 * 1024 * 1024);
    if (!object(result) || !Array.isArray(result.choices)) throw new ProxyError(502, "invalid_provider_response");
    const usage = result.usage?.total_tokens;
    if (Number.isSafeInteger(usage) && usage >= 0 && usage <= checked.reservedTokens) actualTokens = usage;
    // Missing/invalid usage is charged at the reserved upper bound. Never refund
    // on a timeout, unknown provider error, or client disconnect.
    return new Response(JSON.stringify(redact(result, key)), {
      status: 200,
      headers: { "content-type": "application/json", "x-observer-request-id": call },
    });
  } catch (error) {
    if (!upstreamAttempted) actualTokens = 0;
    if (error instanceof ProxyError) throw error;
    if (timedOut(error)) throw new ProxyError(504, "model_provider_timeout");
    throw new ProxyError(502, "model_provider_unavailable");
  } finally {
    // If the database is temporarily unavailable, leave the reservation held.
    // The reconciler conservatively settles it; it must never retry the model.
    await deps.rpc("observer_settle_model", { p_call: call, p_actual_tokens: actualTokens });
  }
}

function decode64(value: string): Uint8Array<ArrayBuffer> {
  try {
    return Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
  } catch {
    throw new ProxyError(503, "credential_configuration_error");
  }
}
function encode64(value: Uint8Array): string {
  return btoa(Array.from(value, (x) => String.fromCharCode(x)).join(""));
}
async function encryptionKey(encoded: string) {
  const bytes = decode64(encoded);
  if (bytes.byteLength !== 32) throw new ProxyError(503, "credential_configuration_error");
  return await crypto.subtle.importKey("raw", bytes, "AES-GCM", false, ["encrypt", "decrypt"]);
}
export async function encryptCredential(plaintext: string, providerId: string, masterKey: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: new TextEncoder().encode(providerId) },
    await encryptionKey(masterKey),
    new TextEncoder().encode(plaintext),
  );
  return "v1." + encode64(iv) + "." + encode64(new Uint8Array(encrypted));
}
export async function decryptCredential(ciphertext: string, providerId: string, masterKey: string): Promise<string> {
  const [version, iv, body, extra] = ciphertext.split(".");
  if (version !== "v1" || !iv || !body || extra) throw new ProxyError(503, "invalid_encrypted_credential");
  try {
    const bytes = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: decode64(iv), additionalData: new TextEncoder().encode(providerId) },
      await encryptionKey(masterKey),
      decode64(body),
    );
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new ProxyError(503, "invalid_encrypted_credential");
  }
}
