/**
 * wechat-qr — upload, replace or delete the caller's WeChat QR code; organizers delete a reported one.
 *
 * POST   (body: the image bytes)  → {path}            checks size, type and that it decodes as a QR code
 * DELETE                          → {removed: bool}    the caller's own code
 * DELETE ?user=<id>               → {removed: bool}    organizers only
 *
 * Authorization: the participant's own Supabase session (Bearer). Objects are written to the private
 * `wechat-qr` bucket with the service role; viewers get short-lived signed URLs (storage policy).
 */
import { createClient } from "npm:@supabase/supabase-js@2";
import { checkQrImage, CONTENT_TYPES, MAX_BYTES } from "../_shared/wechat-qr.ts";

const url = (Deno.env.get("SUPABASE_URL") ?? "").replace(/\/+$/, "");
const anonKey = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const service = createClient(url, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "", { auth: { persistSession: false } });
const BUCKET = "wechat-qr";
const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization, x-client-info, apikey, content-type",
  "access-control-allow-methods": "POST, DELETE, OPTIONS",
  "cache-control": "no-store",
};
const reply = (status: number, body: unknown) => Response.json(body, { status, headers: cors });

async function caller(request: Request): Promise<{ id: string; banned: boolean } | null> {
  const token = (request.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  if (!token || token === anonKey) return null;
  const { data, error } = await createClient(url, anonKey, { auth: { persistSession: false } }).auth.getUser(token);
  if (error || !data.user) return null;
  const { data: profile } = await service.from("profiles").select("is_banned").eq("id", data.user.id).maybeSingle();
  if (!profile) return null;
  return { id: data.user.id, banned: Boolean(profile.is_banned) };
}

async function readBody(request: Request): Promise<Uint8Array | null> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_BYTES) return null;
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const parts: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > MAX_BYTES) {
      await reader.cancel();
      return null;
    }
    parts.push(value);
  }
  const out = new Uint8Array(size);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

async function removeObject(path: string | null) {
  if (path) await service.storage.from(BUCKET).remove([path]);
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  try {
    const me = await caller(request);
    if (!me) return reply(401, { error: "not_authenticated" });
    if (me.banned) return reply(403, { error: "banned" });

    if (request.method === "POST") {
      const bytes = await readBody(request);
      if (!bytes) return reply(413, { error: "too_large" });
      const check = await checkQrImage(bytes);
      if (!check.ok) return reply(422, { error: check.error });
      const path = `${me.id}/${crypto.randomUUID()}.${check.kind}`;
      const up = await service.storage.from(BUCKET).upload(path, bytes, {
        contentType: CONTENT_TYPES[check.kind],
        upsert: false,
      });
      if (up.error) return reply(503, { error: "storage_unavailable" });
      const { data: old, error } = await service.rpc("wechat_qr_store", { p_user: me.id, p_path: path });
      if (error) {
        await removeObject(path);
        return reply(503, { error: "storage_unavailable" });
      }
      await removeObject(old as string | null);
      return reply(200, { path });
    }

    if (request.method === "DELETE") {
      const target = new URL(request.url).searchParams.get("user") ?? me.id;
      if (!/^[0-9a-f-]{36}$/.test(target)) return reply(400, { error: "invalid_user" });
      const { data: old, error } = await service.rpc("wechat_qr_remove", { p_user: target, p_actor: me.id });
      if (error) {
        return reply(error.message === "admin_only" ? 403 : 503, {
          error: error.message === "admin_only" ? "admin_only" : "storage_unavailable",
        });
      }
      await removeObject(old as string | null);
      return reply(200, { removed: Boolean(old) });
    }
    return reply(405, { error: "method_not_allowed" });
  } catch (error) {
    console.error("wechat-qr", error);
    return reply(503, { error: "storage_unavailable" });
  }
});
