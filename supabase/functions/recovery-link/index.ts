/**
 * recovery-link — Supabase Edge Function.
 *
 * POST /recovery-link  { "email": "someone@example.com" }
 *   -> { "email": ..., "action_link": "https://...", "expires_in_seconds": 3600 }
 *
 * Mints a password-recovery link for a participant and hands it back instead of mailing it. The project has no
 * custom SMTP, so the built-in mailer is capped at two messages an hour for the whole project — during a
 * hackathon that means a participant who forgets their password simply never receives the reset mail. An
 * organiser can generate the link here and pass it to them over whatever channel they are already using.
 *
 * Caller must be a signed-in admin: the JWT is verified against the database and `public.is_admin()` decides.
 * The service-role key never leaves the function.
 */

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const SITE_URL = Deno.env.get("RECOVERY_REDIRECT_URL") ?? "https://bh3gei.github.io/agent-observer/reset";

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Max-Age": "86400",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "content-type": "application/json; charset=utf-8" },
  });
}

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method === "OPTIONS") return new Response("ok", { headers: CORS_HEADERS });
  if (request.method !== "POST") return json(405, { message: "method_not_allowed" });

  const authorization = request.headers.get("Authorization") ?? "";
  if (!authorization.startsWith("Bearer ")) return json(401, { message: "not_authenticated" });

  // Verify the caller is an admin using their own JWT, so the check runs through the same is_admin() the rest
  // of the console uses rather than a second copy of the rule.
  const asCaller = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    global: { headers: { Authorization: authorization } },
  });
  const { data: isAdmin, error: adminError } = await asCaller.rpc("is_admin");
  if (adminError) return json(500, { message: adminError.message });
  if (isAdmin !== true) return json(403, { message: "admin_only" });

  let email = "";
  try {
    email = String(((await request.json()) as { email?: unknown })?.email ?? "").trim().toLowerCase();
  } catch {
    return json(400, { message: "invalid_json" });
  }
  if (!email || !email.includes("@")) return json(400, { message: "email_required" });

  const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
  const { data, error } = await admin.auth.admin.generateLink({
    type: "recovery",
    email,
    options: { redirectTo: SITE_URL },
  });
  if (error) return json(400, { message: error.message });

  const link = data?.properties?.action_link ?? "";
  if (!link) return json(500, { message: "no_link_returned" });

  // Leave a trail: handing out a sign-in link is an account-level action. A Postgrest builder is a thenable
  // rather than a real Promise, so this needs try/catch — auditing must never block the organiser.
  try {
    await admin.rpc("audit_admin_action", {
      p_action: "admin.user.recovery_link",
      p_detail: { email },
    });
  } catch {
    // ignored on purpose
  }

  return json(200, { email, action_link: link, expires_in_seconds: 3600 });
});
