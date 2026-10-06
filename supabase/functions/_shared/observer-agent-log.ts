// The team's own project log (build output and stderr, scrubbed by the trusted
// executor) is kept beside, not inside, the immutable result produced by the
// engine. Execute and engine are separate jobs: the engine publishes the result
// before the executor stops the project, so the ZIP is completed on download.
import { appendZipEntry, readZipEntry } from "./observer-zip.ts";

export const AGENT_LOG_BYTES = 2 * 1024 * 1024 + 65536;
export const RESULT_ARCHIVE_BYTES = 52428800;

function run(value: string) {
  if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value)) throw new Error("invalid_run");
  return value;
}
/** Written only by the job API for the executor's own claimed run. */
export const agentLogPath = (id: string) => "agent-logs/" + run(id) + "/agent-log.zip";

export type ResultStorage = {
  download: (path: string) => Promise<Uint8Array | null>;
  upload: (path: string, data: Uint8Array) => Promise<void>;
  /** Short signed URL for an existing object, or null when there is none. */
  sign: (path: string) => Promise<string | null>;
};

/** The team's stored agent.log for a run, or null (local sessions, older runs, damaged log). */
export async function storedAgentLog(id: string, storage: ResultStorage): Promise<Uint8Array | null> {
  const stored = await storage.download(agentLogPath(id));
  if (!stored || stored.length > AGENT_LOG_BYTES + 65536) return null;
  return await readZipEntry(stored, "agent.log", AGENT_LOG_BYTES * 3).catch(() => null);
}

/**
 * Name of the service-written, downloadable copy of one run's result. It is keyed by
 * the result reference (a GitHub commit or storage path) and the exact agent.log
 * added, so a copy found under this name is the current one and is reused as is.
 */
export async function resultCopyPath(id: string, reference: string, log: Uint8Array | null): Promise<string> {
  const head = new TextEncoder().encode(reference + (log ? "\0log\0" : "\0none"));
  const input = new Uint8Array(head.length + (log?.length ?? 0));
  input.set(head);
  if (log) input.set(log, head.length);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", input));
  const key = Array.from(digest.slice(0, 16), (b) => b.toString(16).padStart(2, "0")).join("");
  return "agent-logs/" + run(id) + "/observer-result-" + key + ".zip";
}

/**
 * Signed URL of the result copy (with agent.log when given). An existing copy is
 * signed straight away; otherwise the result is read once from trusted storage or
 * GitHub, completed and stored. Signed storage URLs are readable cross-origin.
 */
export async function resultCopy(
  id: string,
  reference: string,
  log: Uint8Array | null,
  result: () => Promise<Uint8Array>,
  storage: ResultStorage,
): Promise<string> {
  const copy = await resultCopyPath(id, reference, log);
  const existing = await storage.sign(copy);
  if (existing) return existing;
  const archive = await result();
  if (archive.length > RESULT_ARCHIVE_BYTES) throw new Error("result_too_large");
  await storage.upload(copy, log ? await appendZipEntry(archive, "agent.log", log) : archive);
  const url = await storage.sign(copy);
  if (!url) throw new Error("result_sign_failed");
  return url;
}

/** Bounded GET of a signed archive URL, without forwarding credentials or following redirects. */
export async function fetchArchive(url: string, fetcher: typeof fetch = fetch): Promise<Uint8Array> {
  const response = await fetcher(url, { redirect: "error", signal: AbortSignal.timeout(30000) });
  if (!response.ok || !response.body) {
    await response.body?.cancel();
    throw new Error("result_download_failed");
  }
  const reader = response.body.getReader();
  const parts: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > RESULT_ARCHIVE_BYTES) throw new Error("result_too_large");
      parts.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  const out = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.byteLength;
  }
  return out;
}

/**
 * agent.log inside a run's result archive: a colocated run writes it beside
 * decisions.csv (the archive has one wrapper folder), so it is found at the root
 * or one folder deep.
 */
export async function resultAgentLog(archive: Uint8Array): Promise<Uint8Array | null> {
  return await readZipEntry(archive, (name) => /^(?:[^/]+\/)?agent\.log$/.test(name), AGENT_LOG_BYTES * 3);
}

/** Characters of agent.log shown on the page; the whole file is a separate download. */
export const AGENT_LOG_TAIL = 65536;

/**
 * The page's view of a run's agent.log: its last AGENT_LOG_TAIL characters, or the
 * whole file when asked (bounded by AGENT_LOG_BYTES at upload). The runner already
 * replaced the run credential and the team's secret variable values with [REDACTED].
 */
export function agentLogView(log: Uint8Array | null, full: boolean) {
  if (!log) return { available: false };
  const text = new TextDecoder().decode(log);
  if (full || text.length <= AGENT_LOG_TAIL) return { available: true, bytes: log.length, truncated: false, log: text };
  const tail = text.slice(-AGENT_LOG_TAIL);
  // Start at a line boundary: a cut line could begin in the middle of anything.
  return { available: true, bytes: log.length, truncated: true, log: tail.slice(tail.indexOf("\n") + 1) };
}
