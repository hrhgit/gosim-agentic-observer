import { assertEquals, assertRejects } from "@std/assert";
import { decodedRequest, jsonResponse, sessionRequest, waitForStep } from "./observer-session.ts";

const TOKEN = "obs_11111111-1111-4111-8111-111111111111." + "a".repeat(43);

function request(body: unknown) {
  return new Request("https://platform.test/functions/v1/observer-session", {
    method: "POST",
    headers: { authorization: "Bearer " + TOKEN, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

function fakeRpc(states: Record<string, unknown>[]) {
  const calls: string[] = [];
  const rpc = (name: string, _args: Record<string, unknown>) => {
    calls.push(name);
    if (name === "observer_step_state") return Promise.resolve(states.length > 1 ? states.shift() : states[0]);
    if (name === "observer_poll") return Promise.resolve({ response: { ok: true }, sequence: 3 });
    return Promise.resolve(null);
  };
  return { rpc, calls };
}

Deno.test("waitForStep stops as soon as the awaited step exists", async () => {
  const { rpc, calls } = fakeRpc([
    { ready: true, observed: true, answered: false },
    { ready: true, observed: true, answered: false },
    { ready: true, observed: true, answered: true },
  ]);
  await waitForStep(rpc, {}, "engine", { seconds: 5, target: "response" }, Date.now, () => Promise.resolve());
  assertEquals(calls, ["observer_step_state", "observer_step_state", "observer_step_state"]);
});

Deno.test("waitForStep gives up at the requested limit", async () => {
  let clock = 0;
  const { rpc } = fakeRpc([{ ready: true, observed: false, answered: false }]);
  await waitForStep(rpc, {}, "participant", { seconds: 1, target: "observation" }, () => clock, (ms) => {
    clock += ms;
    return Promise.resolve();
  });
  assertEquals(clock, 1000);
});

Deno.test("advance commits, publishes and returns the engine poll in one request", async () => {
  const { rpc, calls } = fakeRpc([{ ready: true, observed: true, answered: true }]);
  const result = await sessionRequest(
    request({
      action: "advance",
      commits: [{ sequence: 2, committed: { rows: [] } }],
      sequence: 3,
      observation: { decision_sequence: 3 },
      wait: 5,
      wait_for: "response",
    }),
    rpc,
  );
  assertEquals(result, { response: { ok: true }, sequence: 3 });
  assertEquals(calls, ["observer_commit_step", "observer_publish_step", "observer_step_state", "observer_poll"]);
});

Deno.test("respond without wait keeps the old single-call behaviour", async () => {
  const { rpc, calls } = fakeRpc([{ ready: true, observed: true, answered: true }]);
  assertEquals(await sessionRequest(request({ action: "respond", sequence: 1, response: {} }), rpc), null);
  assertEquals(calls, ["observer_respond"]);
});

Deno.test("invalid wait values are rejected", async () => {
  const { rpc } = fakeRpc([{ ready: true, observed: true, answered: true }]);
  await assertRejects(() => sessionRequest(request({ action: "poll", wait: -1 }), rpc));
  await assertRejects(() => sessionRequest(request({ action: "poll", wait: 1, wait_for: "anything" }), rpc));
});

Deno.test("gzip request bodies are decoded and large responses compressed on request", async () => {
  const body = new Blob([JSON.stringify({ action: "status" })]).stream().pipeThrough(new CompressionStream("gzip"));
  const decoded = decodedRequest(
    new Request("https://platform.test/", { method: "POST", headers: { "content-encoding": "gzip" }, body }),
  );
  assertEquals(await decoded.json(), { action: "status" });
  const value = { data: "x".repeat(20000) };
  const plain = jsonResponse(value, null, {});
  assertEquals(plain.headers.get("content-encoding"), null);
  const zipped = jsonResponse(value, "gzip, deflate", {});
  assertEquals(zipped.headers.get("content-encoding"), "gzip");
  const text = await new Response(zipped.body!.pipeThrough(new DecompressionStream("gzip"))).text();
  assertEquals(JSON.parse(text), value);
  assertEquals(jsonResponse({ small: 1 }, "gzip", {}).headers.get("content-encoding"), null);
});
