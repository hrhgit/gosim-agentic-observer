import { createClient } from "npm:@supabase/supabase-js@2";
import { GatewayError, handleGateway, type Identity } from "../_shared/cli-gateway.ts";
import { decryptCredential, encryptCredential } from "../_shared/observer-model.ts";

const url = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/+$/, "");
const anonKey = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const masterKey = Deno.env.get("OBSERVER_KEY_ENCRYPTION_KEY") ?? "";
const service = createClient(url, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "", { auth: { persistSession: false } });
const authClient = () => createClient(url, anonKey, { auth: { persistSession: false, autoRefreshToken: false } });
const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization, content-type",
  "access-control-allow-methods": "POST, OPTIONS",
  "cache-control": "no-store",
};
const KNOWN = new Set(["invalid_token", "cli_tokens_disabled", "account_banned", "rate_limited"]);

async function authenticate(hash: string): Promise<Identity> {
  const { data, error } = await service.rpc("cli_token_authenticate", { p_hash: hash });
  if (error) {
    const code = KNOWN.has(error.message) ? error.message : "gateway_unavailable";
    throw new GatewayError(code === "rate_limited" ? 429 : code === "gateway_unavailable" ? 503 : 401, code);
  }
  return data as Identity;
}

type Session = { access_token: string; refresh_token: string; expires_at: number };

async function save(user: string, session: Session) {
  const encrypted = await encryptCredential(JSON.stringify(session), user + ":cli-session", masterKey);
  await service.rpc("cli_session_put", {
    p_user: user,
    p_encrypted: encrypted,
    p_expires_at: new Date(session.expires_at * 1000).toISOString(),
  });
}

function fromAuth(session: { access_token: string; refresh_token: string; expires_at?: number } | null): Session {
  if (!session?.access_token || !session.refresh_token) throw new GatewayError(503, "session_unavailable");
  return {
    access_token: session.access_token,
    refresh_token: session.refresh_token,
    expires_at: session.expires_at ?? Math.floor(Date.now() / 1000) + 3000,
  };
}

/** The owner's server-side session: cached (encrypted), refreshed, or newly started. */
async function userJwt(identity: Identity): Promise<string> {
  const user = identity.user_id;
  const { data: cached } = await service.rpc("cli_session_get", { p_user: user });
  let session: Session | null = null;
  if (cached?.encrypted_session) {
    try {
      session = JSON.parse(await decryptCredential(cached.encrypted_session, user + ":cli-session", masterKey));
    } catch {
      session = null;
    }
  }
  if (session && session.expires_at - 120 > Date.now() / 1000) return session.access_token;
  if (session) {
    const { data, error } = await authClient().auth.refreshSession({ refresh_token: session.refresh_token });
    if (!error && data.session) {
      const next = fromAuth(data.session);
      await save(user, next);
      return next.access_token;
    }
  }
  // A fresh ordinary session for the owner (no e-mail is sent by generateLink).
  const link = await service.auth.admin.generateLink({ type: "magiclink", email: identity.email });
  const hashed = link.data?.properties?.hashed_token;
  if (link.error || !hashed) throw new GatewayError(503, "session_unavailable");
  const verified = await authClient().auth.verifyOtp({ token_hash: hashed, type: "magiclink" });
  if (verified.error) throw new GatewayError(503, "session_unavailable");
  const next = fromAuth(verified.data.session);
  await save(user, next);
  return next.access_token;
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  try {
    if (!masterKey) throw new GatewayError(503, "gateway_unavailable");
    const response = await handleGateway(request, { url, anonKey, authenticate, userJwt });
    for (const [k, v] of Object.entries(cors)) response.headers.set(k, v);
    return response;
  } catch (error) {
    if (!(error instanceof GatewayError)) console.error("survey26-cli", error);
    const known = error instanceof GatewayError;
    return Response.json({ error: known ? error.code : "gateway_unavailable" }, {
      status: known ? error.status : 503,
      headers: cors,
    });
  }
});
