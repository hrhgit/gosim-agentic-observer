/** Organizer-only negative acceptance check against a disposable active run.
 * Read configuration through stdin, never args/files; use a synthetic API key.
 * No score/decision mutation and no real provider credential or model charge.
 */
import { createClient } from "npm:@supabase/supabase-js@2";
import { assertEquals } from "@std/assert";
import { decryptCredential } from "../_shared/observer-model.ts";

const config = await new Response(Deno.stdin.readable).json();
const input = JSON.parse(await decryptCredential(config.encrypted_input, config.job_id, config.master));
const run = input.run_credential.slice(4).split(".")[0];
const client = createClient(config.url, config.anon, { auth: { persistSession: false } });
const portal = async (body: Record<string, unknown>) => {
  const response = await fetch(config.url + "/functions/v1/observer-portal", {
    method: "POST", headers: { "content-type": "application/json", authorization: "Bearer " + config.team_token, apikey: config.anon },
    body: JSON.stringify(body), signal: AbortSignal.timeout(130000),
  });
  const value = await response.json();
  if (!response.ok) throw new Error("portal_failed_" + response.status + "_" + value.error);
  return value.data;
};
// The relay serves only teams that chose not to save a key (this deletes a saved key).
await portal({ action: "set_team_model_mode", mode: "relay" });
const routes = await portal({ action: "model_routes" });
const route = routes.find((r: { run_id: string }) => r.run_id === run);
if (!route) throw new Error("No personal route for the disposable run");
const channel = client.channel(route.topic);
const seen = new Set<string>(), tasks: Promise<unknown>[] = [];
let received = 0, finished = 0;
try {
  await new Promise<void>((resolve, reject) => {
    channel.on("broadcast", { event: "request" }, ({ payload }) => {
      if (payload.run_id !== run || seen.has(payload.call_id)) return;
      seen.add(payload.call_id); received++;
      const task = portal({ action: "personal_model", ...payload, base_url: "https://openrouter.ai/api/v1", model: "openai/gpt-4o-mini", api_key: "observer-invalid-acceptance-" + crypto.randomUUID() })
        .then((result) => { assertEquals(result.completed, false); finished++; });
      tasks.push(task);
    }).subscribe((status) => {
      if (status === "SUBSCRIBED") resolve();
      if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") reject(new Error("relay_subscription_failed"));
    });
  });
  const response = await fetch(config.url + "/functions/v1/observer-model/v1/chat/completions", {
    method: "POST", headers: { authorization: "Bearer " + input.run_credential, "content-type": "application/json", apikey: config.anon },
    body: JSON.stringify({ model: "participant-model", messages: [{ role: "user", content: "Synthetic deployed relay acceptance check" }], max_tokens: 1 }),
    signal: AbortSignal.timeout(135000),
  });
  const result = await response.json();
  assertEquals(response.status, 502);
  assertEquals(result.error.code, "personal_model_failed");
  await Promise.all(tasks);
  assertEquals(received, 1); assertEquals(finished, 1);
  console.log(JSON.stringify({ deployed_relay: "passed", upstream_invalid_key: "rejected", error_response: "redacted", actual_model_credential: "not used" }));
} finally {
  await client.removeAllChannels();
}
