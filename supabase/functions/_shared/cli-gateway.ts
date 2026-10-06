// survey26 command-line gateway: a personal API token in, the website's own requests out.
//
// Every request is authenticated afresh (cli_token_authenticate: hash lookup, rollout switch,
// ban and rate checks, last-use record). The gateway then sends the same requests the website
// sends, with an ordinary session of the token's owner that never leaves the server, so the
// database, RLS and observer-portal apply exactly the website's permissions, quotas and limits.
// Only the contestant actions listed here are reachable; organizer RPCs and token management
// are not.

export class GatewayError extends Error {
  constructor(public status: number, public code: string) {
    super(code);
  }
}

/** RPCs a contestant's website calls (team, invitations, profile, credits, boards, self-check). */
export const ALLOWED_RPCS = new Set([
  "me",
  "current_competition",
  "my_observer_phase",
  "team_members",
  "team_capacity",
  "create_team",
  "join_team",
  "update_team",
  "regenerate_invite_code",
  "transfer_leadership",
  "remove_member",
  "leave_team",
  "disband_team",
  "team_directory",
  "request_team_join",
  "send_team_invite",
  "respond_team_invite",
  "cancel_team_invite",
  "my_team_invitations",
  "mark_team_invitations_read",
  "team_invitation_unread",
  "send_team_invite_by_uid",
  "my_friends",
  "send_friend_request",
  "respond_friend_request",
  "cancel_friend_request",
  "remove_friend",
  "block_user",
  "unblock_user",
  "participants_wall",
  "participants_stats",
  "teammate_contact",
  "clear_my_avatar",
  "kimi_plan_status",
  "claim_kimi_plan_code",
  "redeem_providers",
  "my_redeem_codes",
  "claim_redeem_code",
  "observer_create_repeat_batches",
  "observer_evaluation_quota",
  "observer_final_versions",
  "observer_board",
  "observer_card_board",
  "leaderboard",
]);

/** observer-portal actions of the website's competition workspace. */
export const ALLOWED_PORTAL = new Set([
  "list",
  "upload",
  "submit_repository",
  "submit_zip",
  "approve",
  "evaluate",
  "set_final_version",
  "withdraw",
  "evidence",
  "diagnostics",
  "agent_log",
  "download_result",
  "download_project",
  "team_environment",
  "save_team_variable",
  "delete_team_variable",
  "set_team_variable_flags",
  "set_team_domains",
  "set_team_egress_route",
]);

/** Profile columns the profile and teammates pages write. */
export const PROFILE_FIELDS = new Set([
  "name",
  "nickname",
  "github",
  "affiliation",
  "role",
  "locale",
  "seeking",
  "seeking_count",
  "looking_for_team",
  "astro_level",
  "ai_level",
  "city",
  "contact",
  "blurb",
  "show_on_wall",
]);

const AVATAR_TYPES: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" };
export const AVATAR_MAX_BYTES = 2 * 1024 * 1024;
const BODY_LIMIT = 3 * 1024 * 1024 + 65536;

export type Identity = { user_id: string; token_id: string; email: string };
export type GatewayDeps = {
  /** Public project URL and public (anon) key, the same the website ships. */
  url: string;
  anonKey: string;
  authenticate: (tokenHash: string) => Promise<Identity>;
  /** A valid access token of an ordinary session for this user. */
  userJwt: (identity: Identity) => Promise<string>;
  fetch?: typeof fetch;
};

export async function sha256Hex(text: string): Promise<string> {
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return Array.from(hash, (n) => n.toString(16).padStart(2, "0")).join("");
}

export function bearerToken(header: string | null): string {
  const token = /^Bearer (s26_[0-9a-f]{64})$/.exec(header ?? "")?.[1];
  if (!token) throw new GatewayError(401, "invalid_token");
  return token;
}

async function readBody(request: Request): Promise<Record<string, unknown>> {
  const length = Number(request.headers.get("content-length") ?? "0");
  if (length > BODY_LIMIT) throw new GatewayError(413, "request_too_large");
  const text = await request.text();
  if (text.length > BODY_LIMIT) throw new GatewayError(413, "request_too_large");
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new GatewayError(400, "invalid_request");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new GatewayError(400, "invalid_request");
  return body as Record<string, unknown>;
}

function objectArg(value: unknown): Record<string, unknown> {
  if (value === undefined || value === null) return {};
  if (typeof value !== "object" || Array.isArray(value)) throw new GatewayError(400, "invalid_request");
  return value as Record<string, unknown>;
}

/** A PostgREST/Storage error body → the code the website shows (its message), with HTTP status. */
async function upstreamError(response: Response): Promise<GatewayError> {
  let code = "request_failed";
  try {
    const body = await response.json();
    code = String(body?.message ?? body?.error ?? code);
  } catch { /* keep the generic code */ }
  return new GatewayError(response.status >= 500 ? 503 : 400, code.slice(0, 300));
}

export async function handleGateway(request: Request, d: GatewayDeps): Promise<Response> {
  const send = d.fetch ?? fetch;
  if (request.method !== "POST") throw new GatewayError(405, "method_not_allowed");
  const token = bearerToken(request.headers.get("authorization"));
  const body = await readBody(request);
  const identity = await d.authenticate(await sha256Hex(token));
  const op = typeof body.op === "string" ? body.op : "";
  const jwt = await d.userJwt(identity);
  const headers = (extra: Record<string, string> = {}) => ({
    apikey: d.anonKey,
    authorization: "Bearer " + jwt,
    ...extra,
  });
  const rest = async (path: string, init: RequestInit = {}) => {
    const response = await send(d.url + "/rest/v1/" + path, init);
    if (!response.ok) throw await upstreamError(response);
    const text = await response.text();
    return text ? JSON.parse(text) : null;
  };
  const rpc = (name: string, args: Record<string, unknown>) =>
    rest("rpc/" + name, {
      method: "POST",
      headers: headers({ "content-type": "application/json" }),
      body: JSON.stringify(args),
    });
  const ok = (data: unknown) => Response.json({ data });

  switch (op) {
    case "whoami":
      return ok({ me: await rpc("me", {}), token_id: identity.token_id });
    case "rpc": {
      const name = typeof body.name === "string" ? body.name : "";
      if (!ALLOWED_RPCS.has(name)) throw new GatewayError(403, "action_not_available");
      return ok(await rpc(name, objectArg(body.args)));
    }
    case "portal": {
      const fields = objectArg(body.fields);
      const action = typeof fields.action === "string" ? fields.action : "";
      if (!ALLOWED_PORTAL.has(action)) throw new GatewayError(403, "action_not_available");
      const response = await send(d.url + "/functions/v1/observer-portal", {
        method: "POST",
        headers: headers({ "content-type": "application/json" }),
        body: JSON.stringify(fields),
      });
      const text = await response.text();
      let parsed: { data?: unknown; error?: string } = {};
      try {
        parsed = JSON.parse(text);
      } catch {
        throw new GatewayError(503, "portal_unavailable");
      }
      if (!response.ok || parsed.error) {
        throw new GatewayError(response.ok ? 400 : response.status, String(parsed.error ?? "portal_unavailable"));
      }
      if (action === "upload" && parsed.data && typeof parsed.data === "object") {
        // The signed upload goes straight to Storage, exactly as from the website.
        const slot = parsed.data as { path: string; token: string };
        return ok({
          ...slot,
          upload_url: d.url + "/storage/v1/object/upload/sign/observer-staging/" + slot.path + "?token=" +
            encodeURIComponent(slot.token),
          apikey: d.anonKey,
        });
      }
      return ok(parsed.data ?? null);
    }
    case "profile_update": {
      const fields = objectArg(body.fields);
      const keys = Object.keys(fields);
      if (!keys.length || keys.some((k) => !PROFILE_FIELDS.has(k))) throw new GatewayError(400, "invalid_field");
      const rows = await rest("profiles?id=eq." + identity.user_id, {
        method: "PATCH",
        headers: headers({ "content-type": "application/json", prefer: "return=minimal" }),
        body: JSON.stringify(fields),
      });
      void rows;
      return ok({ me: await rpc("me", {}) });
    }
    case "avatar_upload": {
      const type = typeof body.content_type === "string" ? body.content_type : "";
      const ext = AVATAR_TYPES[type];
      if (!ext) throw new GatewayError(400, "avatar_bad_type");
      let bytes: Uint8Array<ArrayBuffer>;
      try {
        bytes = Uint8Array.from(atob(String(body.data ?? "")), (c) => c.charCodeAt(0));
      } catch {
        throw new GatewayError(400, "avatar_invalid_image");
      }
      if (!bytes.length) throw new GatewayError(400, "avatar_invalid_image");
      if (bytes.length > AVATAR_MAX_BYTES) throw new GatewayError(400, "avatar_too_large");
      const name = identity.user_id + "/" + crypto.randomUUID().replaceAll("-", "").slice(0, 16) + "." + ext;
      const stored = await send(d.url + "/storage/v1/object/avatars/" + name, {
        method: "POST",
        headers: headers({ "content-type": type, "x-upsert": "false" }),
        body: bytes,
      });
      if (!stored.ok) throw await upstreamError(stored);
      const publicUrl = d.url + "/storage/v1/object/public/avatars/" + name;
      try {
        await rpc("set_my_avatar", { p_url: publicUrl });
      } catch (error) {
        await send(d.url + "/storage/v1/object/avatars/" + name, { method: "DELETE", headers: headers() }).catch(
          () => undefined,
        );
        throw error;
      }
      return ok({ avatar_url: publicUrl });
    }
    case "avatar_clear": {
      const me = await rpc("me", {}) as { avatar_url?: string | null } | null;
      await rpc("clear_my_avatar", {});
      const match = /\/storage\/v1\/object\/public\/avatars\/([^?#]+)$/.exec(me?.avatar_url ?? "");
      if (match && match[1].startsWith(identity.user_id + "/")) {
        await send(d.url + "/storage/v1/object/avatars/" + match[1], { method: "DELETE", headers: headers() })
          .catch(() => undefined);
      }
      return ok({ avatar_url: null });
    }
    case "phases":
      return ok(
        await rest(
          "phases?select=id,slug,name_en,name_zh,sort_order,starts_at,ends_at,is_active,leaderboard_mode,counts_for_final," +
            "observer_settings:observer_phase_settings(projects_enabled,daily_batches,sealed,repeat_runs)&order=sort_order",
          { headers: headers() },
        ),
      );
    case "scenarios": {
      const ids = Array.isArray(body.ids) ? body.ids.filter((id) => typeof id === "string") as string[] : [];
      if (ids.length > 100 || ids.some((id) => !/^[0-9a-f-]{36}$/.test(id))) {
        throw new GatewayError(400, "invalid_request");
      }
      if (!ids.length) return ok([]);
      return ok(await rest("scenarios?select=id,slug,name&id=in.(" + ids.join(",") + ")", { headers: headers() }));
    }
    default:
      throw new GatewayError(400, "unknown_operation");
  }
}
