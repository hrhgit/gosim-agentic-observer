import { assertEquals, assertRejects } from "@std/assert";
import { ALLOWED_PORTAL, ALLOWED_RPCS, GatewayError, handleGateway, sha256Hex } from "./cli-gateway.ts";

const TOKEN = "s26_" + "a".repeat(64);
type Call = { url: string; init: RequestInit };

function deps(calls: Call[], respond: (url: string) => Response = () => Response.json({ ok: true })) {
  let authenticated = "";
  return {
    get hash() {
      return authenticated;
    },
    d: {
      url: "https://project.example",
      anonKey: "anon",
      authenticate: async (hash: string) => {
        authenticated = hash;
        if (hash !== await sha256Hex(TOKEN)) throw new GatewayError(401, "invalid_token");
        return { user_id: "11111111-1111-1111-1111-111111111111", token_id: "t", email: "u@example.test" };
      },
      userJwt: () => Promise.resolve("user-jwt"),
      fetch: ((input: string | URL | Request, init?: RequestInit) => {
        calls.push({ url: String(input), init: init ?? {} });
        return Promise.resolve(respond(String(input)));
      }) as typeof fetch,
    },
  };
}
const req = (body: unknown, token = TOKEN) =>
  new Request("https://fn.example", {
    method: "POST",
    headers: { authorization: "Bearer " + token, "content-type": "application/json" },
    body: JSON.stringify(body),
  });

Deno.test("rejects missing or malformed tokens before any lookup", async () => {
  const calls: Call[] = [];
  const { d } = deps(calls);
  for (const token of ["", "eyJhbGciOi.jwt.like", "s26_short"]) {
    const e = await assertRejects(() => handleGateway(req({ op: "whoami" }, token), d), GatewayError);
    assertEquals(e.code, "invalid_token");
  }
  assertEquals(calls.length, 0);
});

Deno.test("only the stored hash is looked up, and an unknown token is refused", async () => {
  const calls: Call[] = [];
  const x = deps(calls);
  const e = await assertRejects(() => handleGateway(req({ op: "whoami" }, "s26_" + "b".repeat(64)), x.d), GatewayError);
  assertEquals(e.code, "invalid_token");
  assertEquals(x.hash, await sha256Hex("s26_" + "b".repeat(64)));
  assertEquals(calls.length, 0);
});

Deno.test("allowed RPCs go to PostgREST with the owner's session; organizer RPCs never do", async () => {
  const calls: Call[] = [];
  const { d } = deps(calls, () => Response.json({ team: 1 }));
  const res = await handleGateway(req({ op: "rpc", name: "join_team", args: { p_invite_code: "ABCD" } }), d);
  assertEquals(await res.json(), { data: { team: 1 } });
  assertEquals(calls[0].url, "https://project.example/rest/v1/rpc/join_team");
  assertEquals((calls[0].init.headers as Record<string, string>).authorization, "Bearer user-jwt");
  for (
    const name of [
      "admin_scenarios",
      "set_competition_mode",
      "create_cli_token",
      "revoke_cli_token",
      "my_cli_tokens",
      "cli_token_authenticate",
      "observer_set_dispatch_limits",
      "set_my_avatar",
    ]
  ) {
    const e = await assertRejects(() => handleGateway(req({ op: "rpc", name }), d), GatewayError);
    assertEquals([e.status, e.code], [403, "action_not_available"]);
  }
  assertEquals(calls.length, 1);
  assertEquals([...ALLOWED_RPCS].some((n) => n.startsWith("admin") || n.includes("cli_token")), false);
});

Deno.test("portal actions are forwarded unchanged; retired or relay actions are refused", async () => {
  const calls: Call[] = [];
  const { d } = deps(calls, () => Response.json({ data: { batch_id: "b" } }));
  const res = await handleGateway(
    req({ op: "portal", fields: { action: "evaluate", phase_id: "p", revision_id: "r" } }),
    d,
  );
  assertEquals(await res.json(), { data: { batch_id: "b" } });
  assertEquals(calls[0].url, "https://project.example/functions/v1/observer-portal");
  assertEquals(JSON.parse(String(calls[0].init.body)), { action: "evaluate", phase_id: "p", revision_id: "r" });
  for (const action of ["personal_model", "model_routes", "local_access", "save_team_model", "admin"]) {
    const e = await assertRejects(() => handleGateway(req({ op: "portal", fields: { action } }), d), GatewayError);
    assertEquals(e.code, "action_not_available");
  }
  assertEquals(ALLOWED_PORTAL.has("personal_model"), false);
});

Deno.test("portal errors keep the website's error code", async () => {
  const { d } = deps([], () => Response.json({ error: "daily_limit" }, { status: 400 }));
  const e = await assertRejects(
    () => handleGateway(req({ op: "portal", fields: { action: "evaluate" } }), d),
    GatewayError,
  );
  assertEquals([e.status, e.code], [400, "daily_limit"]);
});

Deno.test("upload slots carry the direct signed Storage URL", async () => {
  const { d } = deps([], () => Response.json({ data: { id: "u", path: "a/b/source.zip", token: "t k" } }));
  const res = await handleGateway(req({ op: "portal", fields: { action: "upload", purpose: "source" } }), d);
  const data = (await res.json()).data;
  assertEquals(
    data.upload_url,
    "https://project.example/storage/v1/object/upload/sign/observer-staging/a/b/source.zip?token=t%20k",
  );
});

Deno.test("profile updates are limited to the website's profile fields", async () => {
  const calls: Call[] = [];
  const { d } = deps(
    calls,
    (u) => u.includes("rpc/me") ? Response.json({ id: "x" }) : new Response(null, { status: 204 }),
  );
  await handleGateway(req({ op: "profile_update", fields: { nickname: "Vega", show_on_wall: true } }), d);
  assertEquals(calls[0].url, "https://project.example/rest/v1/profiles?id=eq.11111111-1111-1111-1111-111111111111");
  for (const fields of [{ is_admin: true }, { team_id: "x" }, { is_banned: false }, {}]) {
    const e = await assertRejects(() => handleGateway(req({ op: "profile_update", fields }), d), GatewayError);
    assertEquals(e.code, "invalid_field");
  }
});

Deno.test("avatar uploads go to the owner's own folder and are size and type checked", async () => {
  const calls: Call[] = [];
  const { d } = deps(calls, () => Response.json({}));
  await handleGateway(req({ op: "avatar_upload", content_type: "image/png", data: btoa("png-bytes") }), d);
  assertEquals(
    calls[0].url.startsWith("https://project.example/storage/v1/object/avatars/11111111-1111-1111-1111-111111111111/"),
    true,
  );
  assertEquals(calls[1].url, "https://project.example/rest/v1/rpc/set_my_avatar");
  let e = await assertRejects(
    () => handleGateway(req({ op: "avatar_upload", content_type: "image/gif", data: "AA==" }), d),
    GatewayError,
  );
  assertEquals(e.code, "avatar_bad_type");
  e = await assertRejects(
    () =>
      handleGateway(
        req({ op: "avatar_upload", content_type: "image/png", data: btoa("x".repeat(2 * 1024 * 1024 + 1)) }),
        d,
      ),
    GatewayError,
  );
  assertEquals(e.code, "avatar_too_large");
});

Deno.test("unknown operations and non-POST requests are refused", async () => {
  const { d } = deps([]);
  let e = await assertRejects(() => handleGateway(req({ op: "sql" }), d), GatewayError);
  assertEquals(e.code, "unknown_operation");
  e = await assertRejects(() => handleGateway(new Request("https://fn.example"), d), GatewayError);
  assertEquals(e.code, "method_not_allowed");
});
