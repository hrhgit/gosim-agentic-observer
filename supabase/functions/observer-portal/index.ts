import { createClient } from "npm:@supabase/supabase-js@2";
import { portalRequest } from "../_shared/observer-portal.ts";
import { ProxyError } from "../_shared/observer-model.ts";
import { artifactDownload, configuredApp } from "../_shared/observer-app.ts";

const url = Deno.env.get("SUPABASE_URL") ?? "";
const service = createClient(url, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "", { auth: { persistSession: false } });
const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization, apikey, content-type, x-client-info",
  "access-control-allow-methods": "POST, OPTIONS",
  "cache-control": "no-store",
};
const list = (name: string) =>
  (Deno.env.get(name) ?? "").split(",").map((s) => s.trim().replace(/\/+$/, "")).filter(Boolean);
Deno.serve({ port: Number(Deno.env.get("OBSERVER_LISTEN_PORT") ?? 8000) }, async (request) => {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  try {
    if (request.method !== "POST") throw new ProxyError(405, "method_not_allowed");
    const token = /^Bearer (.+)$/.exec(request.headers.get("authorization") ?? "")?.[1];
    if (!token) throw new ProxyError(401, "login_required");
    const { data: account, error } = await service.auth.getUser(token);
    if (error || !account.user) throw new ProxyError(401, "login_required");
    const user = createClient(url, Deno.env.get("SUPABASE_ANON_KEY") ?? "", {
      global: { headers: { Authorization: "Bearer " + token } },
      auth: { persistSession: false },
    });
    const profile = await user.from("profiles").select("team_id,is_banned").eq("id", account.user.id).maybeSingle();
    if (profile.error || !profile.data || profile.data.is_banned) throw new ProxyError(403, "account_unavailable");
    if (!profile.data.team_id) throw new ProxyError(400, "team_required");
    const result = await portalRequest(request, {
      user,
      service,
      userId: account.user.id,
      masterKey: Deno.env.get("OBSERVER_KEY_ENCRYPTION_KEY") ?? "",
      modelBases: list("OBSERVER_MODEL_BASES"),
      httpBases: list("OBSERVER_MODEL_HTTP_BASES"),
      artifactDownload: async (reference) => artifactDownload(await configuredApp(service), reference),
      resolveSource: async (source, options) =>
        (await configuredApp(service)).resolvePublicSource(account.user.id, source, options),
    });
    return Response.json({ data: result }, { headers: cors });
  } catch (error) {
    const body = error instanceof ProxyError
      ? { error: error.code, ...(error.code === "zip_has_no_code" && error.detail ? { detail: error.detail } : {}) }
      : { error: "portal_unavailable" };
    return Response.json(body, {
      status: error instanceof ProxyError ? error.status : 503,
      headers: cors,
    });
  }
});
