import { createClient } from "npm:@supabase/supabase-js@2";
import { ProxyError } from "../_shared/observer-model.ts";
import { decodedRequest, jsonResponse, sessionRequest } from "../_shared/observer-session.ts";

const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "POST, OPTIONS",
  "access-control-allow-headers": "authorization, content-type, apikey",
};
const service = createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "", {
  auth: { persistSession: false },
});
const known = new Set([
  "instance_not_configured",
  "invalid_instance_record",
  "instance_record_conflict",
  "instance_already_published",
  "instance_not_recorded",
  "scenario_not_comparable",
  "invalid_calibrated_score",
  "session_not_finished",
  "decisions_export_too_large",
  "invalid_or_expired_capability",
  "session_deadline",
  "publication_not_ready",
  "participant_not_ready",
  "step_not_published",
  "step_out_of_order",
  "response_conflict",
  "observation_conflict",
  "publication_conflict",
  "commit_conflict",
  "result_conflict",
  "invalid_response",
  "invalid_commit",
  "invalid_result",
  "invalid_observation",
  "invalid_publication",
  "invalid_score",
  "run_not_running",
]);

Deno.serve({ port: Number(Deno.env.get("OBSERVER_LISTEN_PORT") ?? 8000) }, async (request) => {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  try {
    if (request.method !== "POST") throw new ProxyError(405, "method_not_allowed");
    const result = await sessionRequest(decodedRequest(request), async (name, args) => {
      const { data, error } = await service.rpc(name, args);
      if (error) {
        // Privileged logs contain only the fixed RPC name and database code,
        // never request bodies, credentials, catalogs or arbitrary error text.
        if (!known.has(error.message)) {
          console.warn(
            "observer_session_rpc_failed",
            name,
            /^[A-Z0-9]{5,12}$/.test(error.code ?? "") ? error.code : "unknown",
          );
        }
        const code = known.has(error.message) ? error.message : "session_service_unavailable";
        throw new ProxyError(
          code === "invalid_or_expired_capability" || code === "session_deadline"
            ? 401
            : code === "session_service_unavailable"
            ? 503
            : 409,
          code,
        );
      }
      return data;
    });
    return jsonResponse({ data: result }, request.headers.get("accept-encoding"), {
      ...cors,
      "content-type": "application/json",
    });
  } catch (error) {
    return new Response(
      JSON.stringify({ error: error instanceof ProxyError ? error.code : "session_service_unavailable" }),
      {
        status: error instanceof ProxyError ? error.status : 503,
        headers: { ...cors, "content-type": "application/json" },
      },
    );
  }
});
