import type { SupabaseClient } from "npm:@supabase/supabase-js@2";
import { ProxyError } from "./observer-model.ts";
import { MAX_PERSONAL_RESPONSE } from "./observer-personal-model.ts";
/** Client/REST Broadcast is transient. Do not replace it with SQL realtime.send.
 * Channel addresses are unguessable, scoped to a run and only exposed to its team.
 */
export async function sendModelBroadcast(client: SupabaseClient, topic: string, event: string, payload: unknown) {
  const channel = client.channel(topic);
  try {
    if (!(await channel.httpSend(event, payload)).success) {
      throw new ProxyError(503, "model_relay_unavailable");
    }
  } finally {
    await client.removeChannel(channel);
  }
}
export async function exchangeModelBroadcast(
  client: SupabaseClient,
  topic: string,
  call: string,
  payload: Record<string, unknown>,
) {
  const channel = client.channel(topic);
  let repeat: ReturnType<typeof setInterval> | undefined, timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    return await new Promise((resolve, reject) => {
      timeout = setTimeout(() => reject(new ProxyError(503, "personal_api_not_connected")), 125000);
      channel.on("broadcast", { event: "response" }, (message) => {
        const data = message.payload;
        if (!data || data.call_id !== call) return;
        if (new TextEncoder().encode(JSON.stringify(data)).length > MAX_PERSONAL_RESPONSE + 1024) {
          reject(new ProxyError(502, "model_response_too_large"));
          return;
        }
        if (data.error === "personal_model_timeout") {
          reject(new ProxyError(504, "model_provider_timeout"));
          return;
        }
        if (data.error) {
          reject(new ProxyError(502, "personal_model_failed"));
          return;
        }
        resolve(data.result);
      }).subscribe((status) => {
        if (status === "SUBSCRIBED") {
          const publish = () => {
            void channel.send({ type: "broadcast", event: "request", payload }).catch(() => {});
          };
          publish();
          repeat = setInterval(publish, 2000);
        } else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
          reject(new ProxyError(503, "model_relay_unavailable"));
        }
      });
    });
  } finally {
    clearInterval(repeat);
    clearTimeout(timeout);
    await client.removeChannel(channel);
  }
}
