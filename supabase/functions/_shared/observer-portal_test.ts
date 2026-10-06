import { assert, assertEquals, assertRejects } from "@std/assert";
import type { SupabaseClient } from "npm:@supabase/supabase-js@2";
import { decryptCredential, ProxyError } from "./observer-model.ts";
import { keyHint, portalRequest } from "./observer-portal.ts";
import { filesZip, readZipFiles } from "./observer-zip.ts";

const master = btoa("m".repeat(32));
const user = "20000000-0000-4000-8000-000000000001";
const key = "sk-team-fixture-abcdefghijklmnop-9Zq4";
type Call = { client: string; name: string; args: Record<string, unknown> };

function clients(status: unknown = { base_url: "https://api.example.test/v1", model: "glm-4.6", key_hint: "9Zq4" }) {
  const calls: Call[] = [];
  const client = (label: string) =>
    ({
      rpc: (name: string, args: Record<string, unknown> = {}) => {
        calls.push({ client: label, name, args });
        const data = name === "observer_delete_team_model"
          ? true
          : name === "observer_set_team_model_mode"
          ? args.p_mode
          : status;
        return Promise.resolve({ data, error: null });
      },
      storage: { from: () => ({}) },
    }) as unknown as SupabaseClient;
  return { calls, user: client("user"), service: client("service") };
}
function portal(
  body: Record<string, unknown>,
  c = clients(),
  resolve: ((host: string, type: "A" | "AAAA") => Promise<string[]>) | null = null,
) {
  return portalRequest(new Request("https://portal.test", { method: "POST", body: JSON.stringify(body) }), {
    user: c.user,
    service: c.service,
    userId: user,
    masterKey: master,
    modelBases: ["https://api.example.test/v1", "http://organizer-test.example.test/v1"],
    httpBases: ["http://organizer-test.example.test/v1"],
    resolve,
  });
}

Deno.test("a saved key is encrypted before storage and never returned or sent to the database in plaintext", async () => {
  const c = clients();
  const result = await portal({
    action: "save_team_model",
    base_url: "https://api.example.test/v1/",
    model: " glm-4.6 ",
    key: "  " + key + "\n",
  }, c);
  assertEquals(c.calls.map((x) => [x.client, x.name]), [
    ["service", "observer_save_team_model"],
    ["user", "observer_team_model"],
  ]);
  const saved = c.calls[0].args;
  assertEquals(saved.p_user, user);
  assertEquals(saved.p_base, "https://api.example.test/v1");
  assertEquals(saved.p_model, "glm-4.6");
  assertEquals(saved.p_key_hint, "9Zq4");
  // Bound to the provider ID it was stored under; another ID cannot decrypt it.
  assertEquals(await decryptCredential(String(saved.p_encrypted_key), String(saved.p_provider), master), key);
  await assertRejects(() => decryptCredential(String(saved.p_encrypted_key), user, master), ProxyError);
  assert(!JSON.stringify(c.calls).includes(key));
  assert(!JSON.stringify(result).includes(key));
  assert(!JSON.stringify(result).includes(String(saved.p_encrypted_key)));
});

Deno.test("HTTP, unapproved and malformed model settings are refused before encryption or storage", async () => {
  const cases: [Record<string, unknown>, string][] = [
    [{ base_url: "http://organizer-test.example.test/v1", model: "m", key }, "model_destination_not_enabled"],
    [{ base_url: "https://unapproved.example.test/v1", model: "m", key }, "model_destination_not_enabled"],
    [{ base_url: "https://user:pw@api.example.test/v1", model: "m", key }, "model_destination_not_enabled"],
    [{ base_url: "https://api.example.test/v1", model: "", key }, "invalid_team_model"],
    [{ base_url: "https://api.example.test/v1", model: "m\nx", key }, "invalid_team_model"],
    [{ base_url: "https://api.example.test/v1", model: "m", key: "short" }, "invalid_team_model"],
    [{ base_url: "https://api.example.test/v1", model: "m", key: "has space inside-key" }, "invalid_team_model"],
    [{ base_url: "https://api.example.test/v1", model: "m", key: "x".repeat(8193) }, "invalid_team_model"],
  ];
  for (const [fields, code] of cases) {
    const c = clients();
    const error = await assertRejects(() => portal({ action: "save_team_model", ...fields }, c), ProxyError);
    assertEquals(error.code, code);
    assertEquals(c.calls.length, 0);
    assert(!error.message.includes(key));
  }
});

Deno.test("key hints reveal at most four characters and nothing for short keys", () => {
  assertEquals(keyHint("0123456789abcdef"), "cdef");
  assertEquals(keyHint("short-key-12345"), "");
});

Deno.test("deleting and choosing the mode use the caller's own team RPCs", async () => {
  const c = clients();
  assertEquals(await portal({ action: "delete_team_model", team_id: "someone-else" }, c), { deleted: true });
  assertEquals(await portal({ action: "set_team_model_mode", mode: "relay", team_id: "someone-else" }, c), {
    mode: "relay",
  });
  assertEquals(c.calls, [
    { client: "user", name: "observer_delete_team_model", args: {} },
    { client: "user", name: "observer_set_team_model_mode", args: { p_mode: "relay", p_protocol: null } },
  ]);
  for (const mode of ["", "organizer", null]) {
    const rejected = clients();
    const error = await assertRejects(() => portal({ action: "set_team_model_mode", mode }, rejected), ProxyError);
    assertEquals(error.code, "invalid_team_model_mode");
    assertEquals(rejected.calls.length, 0);
  }
});

Deno.test("a repeat evaluation is only requested with an explicit confirmation", async () => {
  const phase = "30000000-0000-4000-8000-000000000001", revision = "30000000-0000-4000-8000-000000000002";
  const c = clients("batch");
  await portal({ action: "evaluate", phase_id: phase, revision_id: revision, confirm_repeat: "yes" }, c);
  await portal({ action: "evaluate", phase_id: phase, revision_id: revision, confirm_repeat: true }, c);
  assertEquals(c.calls.map((x) => x.args), [
    { p_phase: phase, p_revision: revision },
    { p_phase: phase, p_revision: revision, p_confirm_repeat: true },
  ]);
});

Deno.test("an evaluation without a model is requested only with no_model: true", async () => {
  const phase = "30000000-0000-4000-8000-000000000001", revision = "30000000-0000-4000-8000-000000000002";
  const c = clients("batch");
  await portal({ action: "evaluate", phase_id: phase, revision_id: revision, no_model: "yes" }, c);
  await portal({ action: "evaluate", phase_id: phase, revision_id: revision, no_model: true }, c);
  assertEquals(c.calls.map((x) => x.args), [
    { p_phase: phase, p_revision: revision },
    { p_phase: phase, p_revision: revision, p_no_model: true },
  ]);
});

Deno.test("team variables are tagged or switched off by name, without their values", async () => {
  const c = clients({ variables: [] });
  await portal({ action: "set_team_variable_flags", name: "OPENAI_API_KEY", disabled: true }, c);
  await portal({ action: "set_team_variable_flags", name: "MY_TOKEN", model: true }, c);
  assertEquals(c.calls, [
    {
      client: "user",
      name: "observer_set_team_variable_flags",
      args: { p_name: "OPENAI_API_KEY", p_model: null, p_disabled: true },
    },
    {
      client: "user",
      name: "observer_set_team_variable_flags",
      args: { p_name: "MY_TOKEN", p_model: true, p_disabled: null },
    },
  ]);
  for (
    const bad of [
      { name: "lower", disabled: true },
      { name: "OPENAI_API_KEY" },
      { name: "OPENAI_API_KEY", disabled: "yes" },
      { name: "OPENAI_API_KEY", model: 1 },
    ]
  ) {
    const rejected = clients();
    const error = await assertRejects(
      () => portal({ action: "set_team_variable_flags", ...bad }, rejected),
      ProxyError,
    );
    assertEquals(error.code, "invalid_team_variable");
    assertEquals(rejected.calls.length, 0);
  }
  // 「添加模型服务」 tags its variables explicitly.
  const tagged = clients({});
  await portal({ action: "save_team_variable", name: "DEEPSEEK_KEY", value: key, model: true }, tagged);
  assertEquals(tagged.calls[0].args.p_model, true);
  const plain = clients({});
  await portal({ action: "save_team_variable", name: "DEEPSEEK_KEY", value: key }, plain);
  assertEquals("p_model" in plain.calls[0].args, false);
});

Deno.test("withdrawal uses the caller's team RPC and database refusals keep their codes", async () => {
  const revision = "30000000-0000-4000-8000-000000000002";
  const c = clients();
  assertEquals(await portal({ action: "withdraw", revision_id: revision }, c), { accepted: true });
  assertEquals(c.calls, [{ client: "user", name: "observer_withdraw_revision", args: { p_revision: revision } }]);
  await assertRejects(() => portal({ action: "withdraw", revision_id: "not-a-uuid" }, clients()), ProxyError);
  for (const code of ["revision_not_withdrawable", "revision_already_evaluated", "revision_withdrawn", "other"]) {
    const refused = {
      user: { rpc: () => Promise.resolve({ data: null, error: { message: code } }) } as unknown as SupabaseClient,
      service: clients().service,
    };
    const error = await assertRejects(
      () => portal({ action: "withdraw", revision_id: revision }, { ...refused, calls: [] }),
      ProxyError,
    );
    assertEquals(error.code, code === "other" ? "portal_request_failed" : code);
  }
});

Deno.test("result download adds the team's stored agent.log, or falls back to the original result", async () => {
  const { singleFileZip, readZipEntry } = await import("./observer-zip.ts");
  const run = "30000000-0000-4000-8000-000000000001";
  const encode = (s: string) => new TextEncoder().encode(s);
  const objects = new Map<string, Uint8Array>();
  const signed: string[] = [];
  let github = 0;
  const result = await singleFileZip("decisions.csv", encode("night,action\n"));
  const client = (visible: boolean) =>
    ({
      from: (table: string) => {
        assertEquals(table, "observer_runs");
        const query = {
          select: () => query,
          eq: (_column: string, value: string) => {
            assertEquals(value, run);
            return query;
          },
          maybeSingle: () =>
            Promise.resolve({
              data: visible ? { result_path: "github:ORG/participant-x@" + "a".repeat(40) } : null,
              error: null,
            }),
        };
        return query;
      },
      storage: {
        from: (bucket: string) => {
          assertEquals(bucket, "observer-staging");
          return {
            download: (path: string) =>
              Promise.resolve(
                objects.has(path)
                  ? { data: new Blob([objects.get(path)! as Uint8Array<ArrayBuffer>]), error: null }
                  : { data: null, error: { message: "Object not found" } },
              ),
            upload: (path: string, data: Uint8Array, options: Record<string, unknown>) => {
              assertEquals(options, { contentType: "application/zip", upsert: true });
              objects.set(path, data);
              return Promise.resolve({ data: { path }, error: null });
            },
            // Like Supabase Storage: signing a missing object is an error.
            createSignedUrl: (path: string, seconds: number) => {
              assert(seconds <= 120);
              if (!objects.has(path)) return Promise.resolve({ data: null, error: { message: "Object not found" } });
              signed.push(path);
              return Promise.resolve({ data: { signedUrl: "https://storage.test/" + path }, error: null });
            },
          };
        },
      },
    }) as unknown as SupabaseClient;
  const download = (visible = true) =>
    portalRequest(
      new Request("https://portal.test", {
        method: "POST",
        body: JSON.stringify({ action: "download_result", run_id: run }),
      }),
      {
        user: client(visible),
        service: client(visible),
        userId: user,
        masterKey: master,
        modelBases: [],
        httpBases: [],
        artifactDownload: (reference) => {
          assert(reference.startsWith("github:"));
          github++;
          return Promise.resolve("https://codeload.github.com/result.zip");
        },
        fetchArchive: (url) => {
          assertEquals(String(url), "https://codeload.github.com/result.zip");
          return Promise.resolve(new Response(result as Uint8Array<ArrayBuffer>));
        },
      },
    );
  const copies = () => [...objects.keys()].filter((k) => k.includes("/observer-result-"));
  const stored = (response: unknown) =>
    objects.get((response as { url: string }).url.replace("https://storage.test/", ""))!;
  // Another team's run is invisible before any service storage read.
  await assertRejects(() => download(false), ProxyError, "result_not_ready");
  assertEquals([signed, github], [[], 0]);
  // No stored log (local session, older run): the original result, unchanged, but served
  // from storage so the browser may read it cross-origin (codeload.github.com refuses CORS).
  const plain = await download();
  assert((plain as { url: string }).url.startsWith("https://storage.test/agent-logs/" + run + "/observer-result-"));
  assertEquals(stored(plain), result);
  assertEquals(github, 1);
  // A repeat download reuses the stored copy without asking GitHub again.
  assertEquals(await download(), plain);
  assertEquals([github, copies().length], [1, 1]);
  objects.set(
    "agent-logs/" + run + "/agent-log.zip",
    await singleFileZip("agent.log", encode("[platform] project stderr\nhello\n")),
  );
  const withLog = await download();
  assert(withLog !== plain);
  const combined = stored(withLog);
  assertEquals(new TextDecoder().decode((await readZipEntry(combined, "agent.log", 1000))!).endsWith("hello\n"), true);
  assertEquals(new TextDecoder().decode((await readZipEntry(combined, "decisions.csv", 1000))!), "night,action\n");
  assertEquals(await download(), withLog);
  assertEquals(github, 2);
  // A damaged stored log never blocks the trusted result.
  objects.set("agent-logs/" + run + "/agent-log.zip", encode("damaged"));
  assertEquals(await download(), plain);
  assertEquals(github, 2);
});

Deno.test("the final version is set or cleared through the caller's team RPC", async () => {
  const phase = "30000000-0000-4000-8000-000000000001", revision = "30000000-0000-4000-8000-000000000002";
  const c = clients({ revision_id: revision, source: "chosen" });
  assertEquals(await portal({ action: "set_final_version", phase_id: phase, revision_id: revision }, c), {
    final_version: { revision_id: revision, source: "chosen" },
  });
  await portal({ action: "set_final_version", phase_id: phase, revision_id: null }, c);
  await portal({ action: "set_final_version", phase_id: phase }, c);
  assertEquals(c.calls, [
    { client: "user", name: "observer_set_final_version", args: { p_phase: phase, p_revision: revision } },
    { client: "user", name: "observer_set_final_version", args: { p_phase: phase, p_revision: null } },
    { client: "user", name: "observer_set_final_version", args: { p_phase: phase, p_revision: null } },
  ]);
  for (const fields of [{ phase_id: "x", revision_id: revision }, { phase_id: phase, revision_id: "x" }]) {
    const refused = clients();
    await assertRejects(() => portal({ action: "set_final_version", ...fields }, refused), ProxyError);
    assertEquals(refused.calls.length, 0);
  }
  for (const code of ["final_version_locked", "final_phase_invalid", "revision_not_approved", "other"]) {
    const refused = {
      user: { rpc: () => Promise.resolve({ data: null, error: { message: code } }) } as unknown as SupabaseClient,
      service: clients().service,
    };
    const error = await assertRejects(
      () => portal({ action: "set_final_version", phase_id: phase, revision_id: revision }, { ...refused, calls: [] }),
      ProxyError,
    );
    assertEquals(error.code, code === "other" ? "portal_request_failed" : code);
  }
});

Deno.test("a result the participant cannot read (another team, or a sealed hidden run) is never signed", async () => {
  const run = "30000000-0000-4000-8000-000000000003";
  const signed: string[] = [];
  const invisible = {
    from: () => ({
      select: () => ({ eq: () => ({ maybeSingle: () => Promise.resolve({ data: null, error: null }) }) }),
    }),
  } as unknown as SupabaseClient;
  const service = {
    storage: { from: () => ({ createSignedUrl: (path: string) => (signed.push(path), Promise.resolve({})) }) },
  } as unknown as SupabaseClient;
  const error = await assertRejects(
    () =>
      portalRequest(
        new Request("https://portal.test", {
          method: "POST",
          body: JSON.stringify({ action: "download_result", run_id: run }),
        }),
        {
          user: invisible,
          service,
          userId: user,
          masterKey: master,
          modelBases: [],
          httpBases: [],
          artifactDownload: (reference) => (signed.push(reference), Promise.resolve("https://x.test")),
        },
      ),
    ProxyError,
  );
  assertEquals(error.code, "result_not_ready");
  assertEquals(signed, []);
});

Deno.test("a secret team variable is encrypted to its own id; a plain one is stored as given", async () => {
  const c = clients({ variables: [], domains: [] });
  const result = await portal({ action: "save_team_variable", name: "KIMI_API_KEY", value: "  " + key + "\n" }, c);
  assertEquals(c.calls.map((x) => [x.client, x.name]), [
    ["service", "observer_save_team_variable"],
    ["user", "observer_team_environment"],
  ]);
  const saved = c.calls[0].args;
  assertEquals([saved.p_user, saved.p_name, saved.p_secret, saved.p_plain, saved.p_hint], [
    user,
    "KIMI_API_KEY",
    true,
    null,
    "9Zq4",
  ]);
  assertEquals(await decryptCredential(String(saved.p_encrypted), String(saved.p_id), master), key);
  assert(!JSON.stringify(c.calls).includes(key) && !JSON.stringify(result).includes(key));
  const plain = clients({});
  await portal({ action: "save_team_variable", name: "KIMI_MODEL", value: " k3 ", secret: false }, plain);
  assertEquals([plain.calls[0].args.p_secret, plain.calls[0].args.p_plain, plain.calls[0].args.p_encrypted], [
    false,
    "k3",
    null,
  ]);
  for (const name of ["OBSERVER_RUN_TOKEN", "SAC_X", "HTTPS_PROXY", "PATH", "lower", "", "A".repeat(65)]) {
    const bad = clients();
    const error = await assertRejects(() => portal({ action: "save_team_variable", name, value: key }, bad));
    assertEquals((error as ProxyError).code, "invalid_team_variable");
    assertEquals(bad.calls.length, 0);
  }
  const big = clients();
  await assertRejects(() => portal({ action: "save_team_variable", name: "BIG", value: "x".repeat(8193) }, big));
  assertEquals(big.calls.length, 0);
});

Deno.test("allowed domains must be public names that resolve only to public addresses", async () => {
  const dns = (map: Record<string, string[]>) => (host: string, type: "A" | "AAAA") =>
    type === "A" && map[host]
      ? Promise.resolve(map[host])
      : Promise.reject(Object.assign(new Error(), { name: "NotFound" }));
  const ok = clients({});
  await portal(
    { action: "set_team_domains", domains: [" API.Kimi.com. ", "api.deepseek.com", "api.kimi.com"] },
    ok,
    dns({ "api.kimi.com": ["104.18.20.246"], "api.deepseek.com": ["8.8.8.8"] }),
  );
  assertEquals(ok.calls[0], {
    client: "service",
    name: "observer_set_team_domains",
    args: { p_user: user, p_hosts: ["api.kimi.com", "api.deepseek.com"] },
  });
  const cases: [unknown, string][] = [
    [["127.0.0.1"], "invalid_team_domains"],
    [["localhost"], "invalid_team_domains"],
    [["svc.internal"], "invalid_team_domains"],
    [["https://api.kimi.com"], "invalid_team_domains"],
    [Array.from({ length: 11 }, (_, i) => `d${i}.com`), "invalid_team_domains"],
    [["inside.example.com"], "team_domain_not_public"],
    [["metadata.example.com"], "team_domain_not_public"],
    [["missing.example.com"], "team_domain_not_public"],
  ];
  for (const [domains, code] of cases) {
    const c = clients({});
    const error = await assertRejects(() =>
      portal(
        { action: "set_team_domains", domains },
        c,
        dns({ "inside.example.com": ["10.0.0.8"], "metadata.example.com": ["169.254.169.254"] }),
      )
    );
    assertEquals((error as ProxyError).code, code);
    assertEquals(c.calls.length, 0);
  }
});

Deno.test("the egress route is a label the team member's own call saves", async () => {
  const ok = clients({ egress_route: { available: true, route: "cn", auto_fallback: false } });
  await portal({ action: "set_team_egress_route", route: "cn", auto_fallback: false }, ok);
  assertEquals(ok.calls[0], {
    client: "user",
    name: "observer_set_team_egress_route",
    args: { p_route: "cn", p_auto_fallback: false },
  });
  const keep = clients({});
  await portal({ action: "set_team_egress_route", route: "overseas" }, keep);
  assertEquals(keep.calls[0].args, { p_route: "overseas", p_auto_fallback: null });
  for (const body of [{ route: "proxy" }, { route: "CN" }, {}, { route: "cn", auto_fallback: "yes" }]) {
    const bad = clients({});
    const error = await assertRejects(() => portal({ action: "set_team_egress_route", ...body }, bad));
    assertEquals((error as ProxyError).code, "invalid_egress_route");
    assertEquals(bad.calls.length, 0);
  }
});

Deno.test("agent_log shows the run's own agent.log: stored, or read from the result once", async () => {
  const { singleFileZip, appendZipEntry } = await import("./observer-zip.ts");
  const { AGENT_LOG_TAIL } = await import("./observer-agent-log.ts");
  const run = "30000000-0000-4000-8000-000000000001";
  const encode = (s: string) => new TextEncoder().encode(s);
  const objects = new Map<string, Uint8Array>();
  let github = 0, path: string | null = "github:ORG/participant-x@" + "a".repeat(40);
  const folder = "ORG-participant-x-" + "a".repeat(40) + "/";
  const result = await appendZipEntry(
    await singleFileZip(folder + "decisions.csv", encode("night,action\n")),
    "agent.log", // placed inside the result's folder, as the runner does
    encode("[platform] project stderr\nplanner: night 1\n"),
  );
  const client = (visible: boolean) =>
    ({
      from: () => {
        const query = {
          select: () => query,
          eq: () => query,
          maybeSingle: () => Promise.resolve({ data: visible ? { result_path: path } : null, error: null }),
        };
        return query;
      },
      storage: {
        from: () => ({
          download: (name: string) =>
            Promise.resolve(
              objects.has(name)
                ? { data: new Blob([objects.get(name)! as Uint8Array<ArrayBuffer>]), error: null }
                : { data: null, error: { message: "Object not found" } },
            ),
          upload: (name: string, data: Uint8Array) => {
            objects.set(name, data);
            return Promise.resolve({ data: { path: name }, error: null });
          },
          createSignedUrl: () => Promise.resolve({ data: null, error: { message: "unused" } }),
        }),
      },
    }) as unknown as SupabaseClient;
  const read = (fields: Record<string, unknown> = {}, visible = true) =>
    portalRequest(
      new Request("https://portal.test", {
        method: "POST",
        body: JSON.stringify({ action: "agent_log", run_id: run, ...fields }),
      }),
      {
        user: client(visible),
        service: client(visible),
        userId: user,
        masterKey: master,
        modelBases: [],
        httpBases: [],
        artifactDownload: () => {
          github++;
          return Promise.resolve("https://codeload.github.com/result.zip");
        },
        fetchArchive: () => Promise.resolve(new Response(result as Uint8Array<ArrayBuffer>)),
      },
    ) as Promise<{ available: boolean; log?: string; truncated?: boolean; bytes?: number }>;
  // Another team's (or a sealed) run: refused before any storage read.
  await assertRejects(() => read({}, false), ProxyError, "run_not_found");
  await assertRejects(() => read({ run_id: "x" }), ProxyError, "invalid_identifier");
  assertEquals(objects.size, 0);
  // A colocated run: agent.log from inside the result's folder; GitHub is asked once.
  const view = await read();
  assertEquals(view, {
    available: true,
    bytes: 43,
    truncated: false,
    log: "[platform] project stderr\nplanner: night 1\n",
  });
  assertEquals((await read()).log, view.log);
  assertEquals(github, 1);
  // An executor run's stored log wins; a long one is shown as a tail of whole lines.
  const long = "first line\n" + "x".repeat(AGENT_LOG_TAIL) + "\nlast line\n";
  objects.set("agent-logs/" + run + "/agent-log.zip", await singleFileZip("agent.log", encode(long)));
  const tail = await read();
  assertEquals([tail.truncated, tail.log, tail.bytes], [true, "last line\n", long.length]);
  assertEquals((await read({ full: true })).log, long);
  // Nothing to show (no result yet, no stored log): the page explains where logs go.
  objects.clear();
  path = null;
  assertEquals(await read(), { available: false });
});

function snapshotClients(enabled: boolean, uploadError: unknown = null) {
  const calls: Call[] = [];
  const uploads: { bucket: string; path: string; size: number; bytes: Uint8Array }[] = [];
  const client = (label: string) =>
    ({
      rpc: (name: string, args: Record<string, unknown> = {}) => {
        calls.push({ client: label, name, args });
        const data = name === "observer_source_snapshots_enabled"
          ? enabled
          : name === "observer_create_project"
          ? "30000000-0000-4000-8000-000000000003"
          : null;
        return Promise.resolve({ data, error: null });
      },
      storage: {
        from: (bucket: string) => ({
          upload: (path: string, bytes: Uint8Array) => {
            uploads.push({ bucket, path, size: bytes.length, bytes });
            return Promise.resolve({ error: uploadError });
          },
        }),
      },
    }) as unknown as SupabaseClient;
  return { calls, uploads, user: client("user"), service: client("service") };
}
type Resolved = { commit: string; archiveUrl: string; ref?: string | null; subdir?: string | null };
function submitRepository(
  c: ReturnType<typeof snapshotClients>,
  resolveSource?: (url: string, options: { ref?: unknown; subdir?: unknown }) => Promise<Resolved>,
  body: Uint8Array<ArrayBuffer> = new Uint8Array([80, 75, 3, 4, 1, 2, 3]),
  fields: Record<string, unknown> = {},
) {
  return portalRequest(
    new Request("https://portal.test", {
      method: "POST",
      body: JSON.stringify({
        action: "submit_repository",
        title: "Agent",
        url: "https://github.com/example/agent",
        ...fields,
      }),
    }),
    {
      user: c.user,
      service: c.service,
      userId: user,
      masterKey: master,
      modelBases: [],
      httpBases: [],
      resolveSource,
      fetchArchive: (() => Promise.resolve(new Response(body))) as typeof fetch,
    },
  );
}
const commit = "a".repeat(40);

Deno.test("a repository submission stores the zipball of its exact commit before the revision exists", async () => {
  const c = snapshotClients(true);
  const result = await submitRepository(c, (url) => {
    assertEquals(url, "https://github.com/example/agent");
    return Promise.resolve({ commit, archiveUrl: "https://codeload.github.com/example/agent/legacy.zip/" + commit });
  });
  assertEquals(result, { revision_id: "30000000-0000-4000-8000-000000000003", source_commit: commit });
  assertEquals(c.uploads.length, 1);
  assertEquals(c.uploads[0].bucket, "observer-sources");
  assert(c.uploads[0].path.startsWith(user + "/" + commit + "-"));
  assertEquals(c.calls.map((x) => x.name), [
    "observer_source_snapshots_enabled",
    "observer_create_project",
    "observer_record_source_snapshot",
  ]);
  const recorded = c.calls[2].args;
  assertEquals([recorded.p_commit, recorded.p_path, recorded.p_bytes], [commit, c.uploads[0].path, 7]);
  assert(/^[0-9a-f]{64}$/.test(String(recorded.p_sha256)));
});

Deno.test("with snapshots off a repository submission is URL-only as before", async () => {
  const c = snapshotClients(false);
  let resolved = 0;
  const result = await submitRepository(c, () => {
    resolved++;
    return Promise.reject(new Error("unused"));
  });
  assertEquals(result, { revision_id: "30000000-0000-4000-8000-000000000003" });
  assertEquals(resolved, 0);
  assertEquals(c.uploads.length, 0);
});

Deno.test("no revision is created when the source cannot be preserved", async () => {
  const cases: [ReturnType<typeof snapshotClients>, () => Promise<Resolved>, string][] = [
    [
      snapshotClients(true),
      () => Promise.reject(Object.assign(new Error("x"), { code: "private_source_requires_zip" })),
      "private_source_requires_zip",
    ],
    [snapshotClients(true), () => Promise.reject(new Error("github down")), "source_snapshot_unavailable"],
    [
      snapshotClients(true, { message: "storage down" }),
      () => Promise.resolve({ commit, archiveUrl: "https://codeload.github.com/x" }),
      "source_snapshot_unavailable",
    ],
  ];
  for (const [c, resolve, code] of cases) {
    const error = await assertRejects(() => submitRepository(c, resolve), ProxyError);
    assertEquals(error.code, code);
    assert(!c.calls.some((x) => x.name === "observer_create_project"));
  }
});

const wrapped = (files: Record<string, string>) =>
  filesZip(
    Object.entries(files).map(([path, data]) => ({
      path: "example-agent-aaaaaaa/" + path,
      data: new TextEncoder().encode(data),
      executable: path.endsWith(".sh"),
    })),
  ) as Promise<Uint8Array<ArrayBuffer>>;

Deno.test("a branch and folder submission stores only that folder of the resolved commit", async () => {
  const c = snapshotClients(true);
  const archive = await wrapped({ "README.md": "root", "apps/agent/main.py": "print(1)", "apps/agent/run.sh": "x" });
  const result = await submitRepository(
    c,
    (url, options) => {
      assertEquals(url, "https://github.com/example/agent/tree/dev");
      assertEquals(options, { ref: null, subdir: "apps/agent" });
      return Promise.resolve({ commit, archiveUrl: "https://codeload.github.com/x", ref: "dev", subdir: "apps/agent" });
    },
    archive,
    { url: "https://github.com/example/agent/tree/dev", subdir: "/apps/agent/" },
  );
  assertEquals(result, {
    revision_id: "30000000-0000-4000-8000-000000000003",
    source_commit: commit,
    source_ref: "dev",
    source_subdir: "apps/agent",
  });
  // The revision keeps the plain repository URL; ref and folder go with the snapshot.
  assertEquals(c.calls[1].args.p_source_location, "https://github.com/example/agent");
  assertEquals([c.calls[2].args.p_ref, c.calls[2].args.p_subdir], ["dev", "apps/agent"]);
  const stored = c.uploads[0].bytes;
  const files = await readZipFiles(stored, 1 << 20);
  assertEquals(files.map((f) => [f.path, f.executable]), [
    ["example-agent-aaaaaaa/main.py", false],
    ["example-agent-aaaaaaa/run.sh", true],
  ]);
  assertEquals(c.calls[2].args.p_bytes, stored.length);
});

Deno.test("repository source options fail with clear errors and create nothing", async () => {
  const archive = await wrapped({ "README.md": "root" });
  const ok = () =>
    Promise.resolve({ commit, archiveUrl: "https://codeload.github.com/x", ref: null, subdir: "missing" });
  const fail = (code: string) => () => Promise.reject(Object.assign(new Error(code), { code }));
  const cases: [boolean, (() => Promise<Resolved>) | undefined, Record<string, unknown>, string][] = [
    [true, ok, { subdir: "missing" }, "source_subdir_not_found"],
    [true, fail("source_ref_not_found"), { branch: "nope" }, "source_ref_not_found"],
    [true, fail("source_options_conflict"), { branch: "x" }, "source_options_conflict"],
    [true, fail("github_not_found"), {}, "repository_not_found"],
    [true, ok, { branch: "a b" }, "invalid_source_ref"],
    [true, ok, { subdir: "../x" }, "invalid_source_subdir"],
    [true, ok, { url: "https://github.com/example/agent/blob/main/x.py" }, "invalid_repository_url"],
    // Without snapshots (rollback switch) only the plain repository link is accepted.
    [false, undefined, { url: "https://github.com/example/agent/tree/dev" }, "source_options_unavailable"],
    [false, undefined, { subdir: "apps" }, "source_options_unavailable"],
  ];
  for (const [enabled, resolve, fields, code] of cases) {
    const c = snapshotClients(enabled);
    const error = await assertRejects(() => submitRepository(c, resolve, archive, fields), ProxyError);
    assertEquals(error.code, code);
    assertEquals(error.status, 400);
    assert(!c.calls.some((x) => x.name === "observer_create_project"));
    assertEquals(c.uploads.length, 0);
  }
});

Deno.test("a ZIP without any program is refused before a revision exists; a missing manifest only warns", async () => {
  const { filesZip } = await import("./observer-zip.ts");
  const { inspectSourceNames } = await import("./observer-source-check.ts");
  const zip = (paths: string[]) =>
    filesZip(paths.map((path) => ({ path, data: new TextEncoder().encode("x"), executable: false })));
  const upload = async (archive: Uint8Array) => {
    const calls: Call[] = [];
    const client = (label: string) =>
      ({
        rpc: (name: string, args: Record<string, unknown> = {}) => {
          calls.push({ client: label, name, args });
          const data = name === "observer_upload_access"
            ? user + "/30000000-0000-4000-8000-000000000009/source.zip"
            : name === "observer_submit_zip"
            ? "30000000-0000-4000-8000-000000000010"
            : null;
          return Promise.resolve({ data, error: null });
        },
        storage: {
          from: () => ({
            list: () =>
              Promise.resolve({ data: [{ name: "source.zip", metadata: { size: archive.length } }], error: null }),
            download: () => Promise.resolve({ data: new Blob([archive as Uint8Array<ArrayBuffer>]), error: null }),
          }),
        },
      }) as unknown as SupabaseClient;
    const c = { calls, user: client("user"), service: client("service") };
    const request = new Request("https://portal.test", {
      method: "POST",
      body: JSON.stringify({ action: "submit_zip", title: "Agent", upload_id: "30000000-0000-4000-8000-000000000009" }),
    });
    const deps = { user: c.user, service: c.service, userId: user, masterKey: master, modelBases: [], httpBases: [] };
    return { c, run: () => portalRequest(request, deps) };
  };
  const empty = await upload(await zip(["proj/.gitignore", "proj/agent/.env.example", "proj/.venv/x.py"]));
  const error = await assertRejects(() => empty.run(), ProxyError);
  assertEquals(error.code, "zip_has_no_code");
  assertEquals(error.detail, { files: ["proj/.gitignore", "proj/agent/.env.example", "proj/.venv/x.py"] });
  assert(!empty.c.calls.some((x) => x.name === "observer_submit_zip"));
  const code = await upload(await zip(["proj/agent.py", "proj/README.md"]));
  assertEquals(await code.run(), { revision_id: "30000000-0000-4000-8000-000000000010", warning: "no_manifest" });
  const manifest = await upload(await zip(["proj/observer.project.json", "proj/run.sh"]));
  assertEquals(await manifest.run(), { revision_id: "30000000-0000-4000-8000-000000000010" });
  assertEquals(inspectSourceNames(["a/Cargo.toml"]).code, true);
  assertEquals(inspectSourceNames(["__MACOSX/a.py", ".git/hooks/x.sh", "a/README.md"]).code, false);
});

Deno.test("the workspace list shows https model bases and the team's own providers only", async () => {
  const query = () => {
    const chain: Record<string, unknown> = {};
    for (const step of ["select", "order", "limit", "eq"]) chain[step] = () => chain;
    chain.maybeSingle = () => Promise.resolve({ data: { team_id: "t1" }, error: null });
    chain.then = (resolve: (v: unknown) => void) => resolve({ data: [], error: null });
    return chain;
  };
  const rpc = (name: string) =>
    Promise.resolve({
      data: name === "observer_list_providers"
        ? [
          { id: "a", base_url: "http://relay.organizer.test:8000/v1", shared: true },
          { id: "b", base_url: "https://api.example.test/v1", shared: false },
        ]
        : null,
      error: null,
    });
  const client = { from: query, rpc, storage: { from: () => ({}) } } as unknown as SupabaseClient;
  const data = await portalRequest(
    new Request("https://portal.test", { method: "POST", body: JSON.stringify({ action: "list" }) }),
    {
      user: client,
      service: client,
      userId: user,
      masterKey: master,
      modelBases: ["https://api.example.test/v1", "http://relay.organizer.test:8000/v1"],
      httpBases: ["http://relay.organizer.test:8000/v1"],
    },
  ) as { model_bases: string[]; providers: { id: string }[] };
  assertEquals(data.model_bases, ["https://api.example.test/v1"]);
  assertEquals(data.providers.map((p) => p.id), ["b"]);
  assert(!JSON.stringify(data).includes("relay.organizer.test"));
});

Deno.test("the workspace list shows the caller's own team only, also for an organizer account", async () => {
  const filters: Record<string, [string, unknown][]> = {};
  const query = (table: string) => {
    const chain: Record<string, unknown> = {};
    filters[table] = [];
    for (const step of ["select", "order", "limit"]) chain[step] = () => chain;
    chain.eq = (column: string, value: unknown) => (filters[table].push([column, value]), chain);
    chain.maybeSingle = () => Promise.resolve({ data: { team_id: "own-team" }, error: null });
    chain.then = (resolve: (v: unknown) => void) => resolve({ data: [], error: null });
    return chain;
  };
  const rpc = () => Promise.resolve({ data: null, error: null });
  const client = { from: query, rpc, storage: { from: () => ({}) } } as unknown as SupabaseClient;
  await portalRequest(
    new Request("https://portal.test", { method: "POST", body: JSON.stringify({ action: "list" }) }),
    { user: client, service: client, userId: user, masterKey: master, modelBases: [], httpBases: [] },
  );
  assertEquals(filters.profiles, [["id", user]]);
  assertEquals(filters.observer_projects, [["team_id", "own-team"]]);
  assertEquals(filters.observer_batches, [["team_id", "own-team"], ["purpose", "formal"]]);
});
