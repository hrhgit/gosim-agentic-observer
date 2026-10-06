/**
 * OpenAI-compatible chat client for the pro agent (Node built-ins only: fetch, no SDK).
 *
 * Configuration (environment, or a local .env next to package.json that is never packed):
 *   OPENAI_API_KEY    required (KIMI_API_KEY is accepted as an alternate name)
 *   OPENAI_BASE_URL   default https://api.kimi.com/coding/v1 (Kimi Coding Plan; outside mainland China
 *                     use https://api.kimi.ai/coding/v1). On the platform this is injected automatically.
 *   OPENAI_MODEL      default k3
 *
 * Calls never block the decision loop: `submit()` starts the request in the background (the event loop
 * runs it while the agent waits for the next stdin line) and returns a handle; the agent picks the answer
 * up on a later decision. HTTP 429 / 5xx, timeouts and network errors are retried with backoff (honouring
 * Retry-After). k3 only accepts the default temperature, so none is sent.
 *
 * Port of python-pro/llm_client.py (background threads there, promises here).
 */
import { readFileSync } from "node:fs";

const DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1";
const DEFAULT_MODEL = "k3";
const MAX_BACKOFF_SECONDS = 20.0;

export type Json = Record<string, unknown>;

export function apiKey(): string {
  return (process.env.OPENAI_API_KEY ?? "").trim() || (process.env.KIMI_API_KEY ?? "").trim();
}

/** Fill missing environment variables from a local .env (for local runs only). */
export function loadDotenv(path: string): void {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return;
  }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const at = line.indexOf("=");
    const name = line.slice(0, at).trim();
    const value = line.slice(at + 1).trim().replace(/^["']+|["']+$/g, "");
    if (name && value && !process.env[name]) process.env[name] = value;
  }
}

const sleep = (seconds: number) => new Promise<void>((resolve) => setTimeout(resolve, Math.max(0, seconds) * 1000));

/** 429, 5xx, timeout, network trouble or an unusable reply: worth another try after a pause. */
class RetryableError extends Error {
  constructor(message: string, readonly retryAfterSeconds: number | null = null) {
    super(message);
  }
}

/** One background chat completion. */
export class Call {
  answer: Json | null = null;
  error: string | null = null;
  seconds = 0.0;
  logged = false;
  private finished = false;
  private readonly promise: Promise<void>;

  constructor(client: LLMClient, readonly tag: string, system: string, user: Json, timeout: number) {
    this.promise = this.run(client, system, user, timeout).finally(() => {
      this.finished = true;
    });
  }

  private async run(client: LLMClient, system: string, user: Json, timeout: number): Promise<void> {
    const started = performance.now();
    const deadline = started + timeout * 1000;
    for (let attempt = 0; attempt < client.maxRetries; attempt++) {
      try {
        this.answer = await client.request(system, user, (deadline - performance.now()) / 1000);
        this.error = null;
        break;
      } catch (exc) {
        this.error = exc instanceof Error ? exc.message || exc.name : String(exc);
        if (!(exc instanceof RetryableError)) break; // 400/401/403/404: retrying will not help
        const pause = Math.min(MAX_BACKOFF_SECONDS, exc.retryAfterSeconds ?? 1.0 + attempt + Math.random());
        if (performance.now() + pause * 1000 > deadline - 1000) break;
        await sleep(pause); // waiting: no CPU budget is charged
      }
    }
    this.seconds = (performance.now() - started) / 1000;
  }

  done(): boolean {
    return this.finished;
  }

  /** Wait up to `seconds` for the answer; true when it is there. */
  async wait(seconds: number): Promise<boolean> {
    if (this.finished || seconds <= 0) return this.finished;
    let timer: NodeJS.Timeout | undefined;
    await Promise.race([this.promise, new Promise<void>((resolve) => (timer = setTimeout(resolve, seconds * 1000)))]);
    clearTimeout(timer);
    return this.finished;
  }
}

export class LLMClient {
  readonly baseUrl: string;
  readonly model: string;
  readonly maxRetries: number;
  private readonly key: string;
  private readonly calls: Call[] = [];
  ok = 0;
  failed = 0;

  constructor(
    private readonly log: (text: string) => void = () => {},
    private readonly callTimeout = 90.0,
    private readonly maxCalls = 1500,
    maxRetries = 3,
    private readonly maxInFlight = 4,
  ) {
    this.baseUrl = (process.env.OPENAI_BASE_URL ?? "").trim().replace(/\/+$/, "") || DEFAULT_BASE_URL;
    this.key = apiKey();
    this.model = (process.env.OPENAI_MODEL ?? "").trim() || DEFAULT_MODEL;
    this.maxRetries = maxRetries;
  }

  /** One HTTP attempt -> the JSON object in the reply. */
  async request(system: string, user: Json, timeout: number): Promise<Json> {
    if (timeout <= 0) throw new Error("timeout");
    let response: Response;
    try {
      response = await fetch(this.baseUrl + "/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + this.key },
        body: JSON.stringify({
          model: this.model,
          messages: [
            { role: "system", content: system },
            { role: "user", content: JSON.stringify(user) },
          ],
          max_tokens: 2000,
        }),
        signal: AbortSignal.timeout(Math.max(1, Math.round(timeout * 1000))),
      });
    } catch (exc) {
      throw new RetryableError(exc instanceof Error ? exc.name : "network error"); // timeout, reset, DNS ...
    }
    if (response.status === 429 || response.status >= 500) {
      const header = response.headers.get("retry-after");
      const retryAfter = header === null || header.trim() === "" ? NaN : Number(header);
      throw new RetryableError(`HTTP ${response.status}`, Number.isFinite(retryAfter) && retryAfter >= 0 ? retryAfter : null);
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    let data: { choices?: { message?: { content?: string | null } }[] };
    try {
      data = (await response.json()) as typeof data;
    } catch {
      throw new RetryableError("reply is not JSON");
    }
    const text = data.choices?.[0]?.message?.content ?? "";
    const match = /\{[\s\S]*\}/.exec(text);
    if (!match) throw new RetryableError("no JSON object in the reply");
    let parsed: unknown;
    try {
      parsed = JSON.parse(match[0]);
    } catch {
      throw new RetryableError("invalid JSON in the reply");
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new RetryableError("reply is not a JSON object");
    return parsed as Json;
  }

  inFlight(): number {
    return this.calls.filter((call) => !call.done()).length;
  }

  /** Start a call in the background; null when the run's limits say no. */
  submit(tag: string, system: string, user: Json, wallclockLeft: number): Call | null {
    const timeout = Math.min(this.callTimeout, wallclockLeft - 30.0);
    if (this.calls.length >= this.maxCalls || timeout < 5.0 || this.inFlight() >= this.maxInFlight) return null;
    const call = new Call(this, tag, system, user, timeout);
    this.calls.push(call);
    return call;
  }

  /** The parsed answer of a finished call (null while running or after a failure). Logs once. */
  collect(call: Call | null): Json | null {
    if (call === null || !call.done()) return null;
    if (!call.logged) {
      call.logged = true;
      if (call.answer !== null) this.ok += 1;
      else {
        this.failed += 1;
        this.log(`llm: ${call.tag} failed (${call.error}); rules decide`); // never log the key
      }
    }
    return call.answer;
  }
}
