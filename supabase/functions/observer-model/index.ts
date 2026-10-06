import { createClient } from "npm:@supabase/supabase-js@2";
import {
  capability,
  chatCompletion,
  decryptCredential,
  ProxyError,
  teamChatCompletion,
  teamMessages,
} from "../_shared/observer-model.ts";
import { personalChat, personalMessages } from "../_shared/observer-personal-model.ts";
import { exchangeModelBroadcast } from "../_shared/observer-model-broadcast.ts";

const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "POST, OPTIONS",
  "access-control-allow-headers":
    "authorization, content-type, apikey, idempotency-key, x-api-key, anthropic-version, anthropic-beta",
  "access-control-expose-headers": "x-observer-request-id",
};
const service = createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "", {
  auth: { persistSession: false },
});
const bases = (key: string) =>
  new Set((Deno.env.get(key) ?? "").split(",").map((s) => s.trim().replace(/\/$/, "")).filter(Boolean));
const knownErrors: Record<string, [number, string]> = {
  invalid_or_expired_capability: [401, "invalid_or_expired_capability"],
  session_deadline: [401, "session_deadline"],
  run_model_quota: [429, "run_model_quota"],
  provider_model_quota: [429, "provider_model_quota"],
  model_not_available: [403, "model_not_available"],
  request_id_conflict: [409, "request_id_conflict"],
  team_model_not_configured: [403, "team_model_not_configured"],
  personal_api_required: [403, "team_model_not_configured"],
  // Teams on team egress call their own provider directly (20261004080000).
  model_proxy_retired: [410, "model_proxy_retired"],
};

Deno.serve({ port: Number(Deno.env.get("OBSERVER_LISTEN_PORT") ?? 8000) }, async (request) => {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  try {
    const pathname = new URL(request.url).pathname;
    const isMessages = pathname.endsWith("/v1/messages");
    const isChat = pathname.endsWith("/v1/chat/completions");
    if (request.method !== "POST" || !(isMessages || isChat)) {
      throw new ProxyError(404, "endpoint_not_found");
    }
    const rpc = async (name: string, args: Record<string, unknown>) => {
      const { data, error } = await service.rpc(name, args);
      if (error) {
        const known = knownErrors[error.message];
        throw new ProxyError(known?.[0] ?? 503, known?.[1] ?? "model_accounting_unavailable");
      }
      return data;
    };
    const decrypt = (value: string, provider: string) =>
      decryptCredential(value, provider, Deno.env.get("OBSERVER_KEY_ENCRYPTION_KEY") ?? "");
    // The Anthropic SDK authenticates with a bare x-api-key header; the OpenAI
    // SDK with Authorization: Bearer. Either is accepted for the scoped run token.
    const scope = capability(request.headers.get("authorization"), request.headers.get("x-api-key"));
    const route = await rpc("observer_model_route", { p_run: scope.run, p_token: scope.token });
    // The saved team provider speaks exactly one protocol; the platform never
    // translates between the OpenAI and Anthropic request shapes.
    if (route.personal && route.protocol && route.protocol !== (isMessages ? "anthropic" : "openai")) {
      throw new ProxyError(400, "protocol_mismatch");
    }
    // Organizer-credit runs (practice on house credits) stay OpenAI-compatible only.
    if (!route.personal && isMessages) throw new ProxyError(400, "protocol_not_supported");
    // Formal runs use the team's choice: its saved key server-side (default), or
    // the relay to its open page. Neither ever falls back to organizer credits.
    const response = !route.personal
      ? await chatCompletion(request, {
        rpc,
        fetch,
        decrypt,
        allowedBases: bases("OBSERVER_MODEL_BASES"),
        allowedHttpBases: bases("OBSERVER_MODEL_HTTP_BASES"),
        defaultProvider: Deno.env.get("OBSERVER_DEFAULT_MODEL_PROVIDER") ?? "",
      })
      : route.mode === "relay"
      ? await (isMessages ? personalMessages : personalChat)(request, route.topic, {
        rpc,
        exchange: (topic, call, payload) => exchangeModelBroadcast(service, topic, call, payload),
      })
      : await (isMessages ? teamMessages : teamChatCompletion)(request, {
        rpc,
        fetch,
        decrypt,
        trustedBases: bases("OBSERVER_MODEL_BASES"),
      });
    for (const [name, value] of Object.entries(cors)) response.headers.set(name, value);
    return response;
  } catch (error) {
    const status = error instanceof ProxyError ? error.status : 503;
    const code = error instanceof ProxyError ? error.code : "model_proxy_unavailable";
    const detail = error instanceof ProxyError ? error.detail ?? {} : {};
    // Never forward database/provider exception messages, headers or credentials.
    return new Response(JSON.stringify({ error: { type: "observer_error", code, message: code, ...detail } }), {
      status,
      headers: { ...cors, "content-type": "application/json" },
    });
  }
});
