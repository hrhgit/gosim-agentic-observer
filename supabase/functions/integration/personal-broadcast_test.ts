/** Explicit opt-in: real transient Realtime transport, synthetic data only.
 * Uses the public anon credential. No account, model charge or database write.
 */
import { createClient } from "npm:@supabase/supabase-js@2";
import { assertEquals, assertRejects } from "@std/assert";
import { exchangeModelBroadcast, sendModelBroadcast } from "../_shared/observer-model-broadcast.ts";
import { fulfillPersonalModel, personalChat } from "../_shared/observer-personal-model.ts";

Deno.test("real Broadcast carries personal model success and failure without the key", async () => {
  const url = Deno.env.get("OBSERVER_RELAY_TEST_URL")!;
  const anon = Deno.env.get("OBSERVER_RELAY_TEST_ANON")!;
  if (!url || !anon) throw new Error("Explicit public Realtime test configuration required");
  const clients = Array.from({ length: 3 }, () => createClient(url, anon, { auth: { persistSession: false } }));
  const [engine, portal, browser] = clients;
  const topic = "observer-test-" + crypto.randomUUID();
  const run = crypto.randomUUID(), key = "synthetic-only-" + crypto.randomUUID();
  const statuses: string[] = [], persisted: unknown[] = [], received: unknown[] = [];
  let failure = false, providerCalls = 0;
  const channel = browser.channel(topic);
  const seen = new Set<string>();
  const completions: Promise<unknown>[] = [];
  try {
    await new Promise<void>((resolve, reject) => {
      channel.on("broadcast", { event: "request" }, ({ payload }) => {
        received.push(payload);
        if (seen.has(payload.call_id)) return;
        seen.add(payload.call_id);
        const task = fulfillPersonalModel(
          { ...payload, base_url: "https://provider.example/v1", model: "own-model", api_key: key },
          "test-owner",
          {
            trustedBases: new Set(["https://provider.example/v1"]),
            rpc: async (name, args) => {
              persisted.push(args);
              if (name === "observer_claim_personal_model") {
                return topic;
              }
              statuses.push(String(args.p_status));
            },
            fetch: async (_url, options) => {
              providerCalls++;
              assertEquals(new Headers(options?.headers).get("authorization"), "Bearer " + key);
              assertEquals(options?.redirect, "error");
              assertEquals(JSON.parse(String(options?.body)).model, "own-model");
              return failure
                ? new Response(key, { status: 401 })
                : Response.json({ choices: [{ message: { content: "OK " + key } }] });
            },
            send: (name, event, payload) => sendModelBroadcast(portal, name, event, payload),
          },
        );
        completions.push(task);
      }).subscribe((status) => {
        if (status === "SUBSCRIBED") resolve();
        if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") reject(new Error("Realtime subscription failed"));
      });
    });
    const call = () =>
      personalChat(
        new Request("https://proxy.example/v1/chat/completions", {
          method: "POST",
          headers: { authorization: `Bearer obs_${run}.${"x".repeat(43)}` },
          body: JSON.stringify({
            model: "agent-default",
            messages: [{ role: "user", content: "Synthetic relay check" }],
            max_tokens: 16,
          }),
        }),
        topic,
        {
          rpc: async (_name, args) => {
            persisted.push(args);
            return true;
          },
          exchange: (name, id, payload) => exchangeModelBroadcast(engine, name, id, payload),
        },
      );
    const response = await call();
    assertEquals((await response.json()).choices[0].message.content, "OK [REDACTED]");
    failure = true;
    await assertRejects(call, Error, "personal_model_failed");
    await Promise.all(completions);
    assertEquals(providerCalls, 2);
    assertEquals(statuses, ["done", "failed"]);
    assertEquals(JSON.stringify(persisted).includes(key), false);
    assertEquals(JSON.stringify(received).includes(key), false);
  } finally {
    await Promise.all(clients.map((client) => client.removeAllChannels()));
  }
});
