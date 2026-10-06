/**
 * Where a team's own model key may be sent: any public OpenAI-compatible HTTPS base.
 *
 * There is no provider allowlist for participants. Instead the base must be an HTTPS
 * URL on the default port whose host is a public DNS name (no IP literal, no local or
 * reserved name), and every address that name resolves to must be public. Exact bases
 * the organizers configure (OBSERVER_MODEL_BASES) are trusted as they are. Redirects
 * stay disabled at every call site, and TLS certificate checks bind the connection to
 * the name, so a name that later resolves elsewhere still cannot reach an internal
 * HTTPS service.
 */
export type Resolver = (host: string, type: "A" | "AAAA") => Promise<string[]>;

const RESERVED_SUFFIXES = [
  "localhost",
  "local",
  "internal",
  "intranet",
  "lan",
  "home.arpa",
  "arpa",
  "test",
  "example",
  "invalid",
  "onion",
];

/** Syntax and naming rules only; null when the base can never be used. */
export function publicBaseSyntax(value: unknown, trusted: Set<string> = new Set()): string | null {
  if (typeof value !== "string" || value.length > 1000) return null;
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) return null;
  const normalized = url.href.replace(/\/+$/, "");
  if (trusted.has(normalized)) return normalized;
  // The URL parser already rewrote every IPv4 spelling (decimal, hex, octal) to dotted form.
  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  if (url.port || !host.includes(".") || host.startsWith("[") || /^[\d.]+$/.test(host)) return null;
  if (RESERVED_SUFFIXES.some((suffix) => host === suffix || host.endsWith("." + suffix))) return null;
  return normalized;
}

function ipv4(address: string): number[] | null {
  const parts = address.split(".");
  if (parts.length !== 4 || parts.some((p) => !/^\d{1,3}$/.test(p) || Number(p) > 255)) return null;
  return parts.map(Number);
}

function publicIpv4([a, b, c]: number[]): boolean {
  return !(
    a === 0 || a === 10 || a === 127 || a >= 224 || // this network, private, loopback, multicast, reserved
    (a === 100 && b >= 64 && b <= 127) || // shared address space (CGNAT)
    (a === 169 && b === 254) || // link-local, including cloud metadata
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 192 && b === 0 && (c === 0 || c === 2)) || // IETF assignments, documentation
    (a === 192 && b === 88 && c === 99) || // 6to4 relay
    (a === 198 && (b === 18 || b === 19)) || // benchmarking
    (a === 198 && b === 51 && c === 100) ||
    (a === 203 && b === 0 && c === 113)
  );
}

function ipv6(address: string): number[] | null {
  let text = address.toLowerCase().replace(/^\[|\]$/g, "").split("%")[0];
  const tail = /(\d+\.\d+\.\d+\.\d+)$/.exec(text);
  if (tail) {
    const v4 = ipv4(tail[1]);
    if (!v4) return null;
    text = text.slice(0, -tail[1].length) + ((v4[0] << 8) | v4[1]).toString(16) + ":" +
      ((v4[2] << 8) | v4[3]).toString(16);
  }
  const halves = text.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const rest = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const missing = 8 - head.length - rest.length;
  if ((halves.length === 1 && missing !== 0) || (halves.length === 2 && missing < 1)) return null;
  const groups = [...head, ...Array(halves.length === 2 ? missing : 0).fill("0"), ...rest];
  if (groups.some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
  return groups.map((g) => parseInt(g, 16));
}

/** Only global unicast (2000::/3), minus documentation, Teredo/IETF and 6to4 ranges. */
function publicIpv6(groups: number[]): boolean {
  const [g0, g1] = groups;
  if ((g0 & 0xe000) !== 0x2000) return false;
  if (g0 === 0x2001 && (g1 === 0x0db8 || g1 < 0x0200)) return false;
  return g0 !== 0x2002;
}

export function isPublicAddress(address: string): boolean {
  const v4 = ipv4(address);
  if (v4) return publicIpv4(v4);
  const v6 = ipv6(address);
  return v6 ? publicIpv6(v6) : false;
}

function denoResolver(): Resolver | null {
  const deno = (globalThis as { Deno?: { resolveDns?: unknown } }).Deno;
  return typeof deno?.resolveDns === "function"
    ? (host, type) => (deno.resolveDns as (h: string, t: string) => Promise<string[]>)(host, type)
    : null;
}

/**
 * Resolve the host and require every address to be public. A name with no A or AAAA record is
 * refused. When the runtime has no working DNS API (every lookup fails for another reason),
 * the naming rules above and the TLS name check still apply.
 */
export async function assertPublicHost(base: string, resolve: Resolver | null = denoResolver()): Promise<void> {
  if (!resolve) return;
  const host = new URL(base).hostname.replace(/\.$/, "");
  const lookup = async (type: "A" | "AAAA") => {
    try {
      return { addresses: await resolve(host, type), absent: false };
    } catch (error) {
      return { addresses: [] as string[], absent: error instanceof Error && error.name === "NotFound" };
    }
  };
  const answers = [await lookup("A"), await lookup("AAAA")];
  const addresses = answers.flatMap((answer) => answer.addresses);
  if (!addresses.length && !answers.some((answer) => answer.absent)) return;
  if (!addresses.length || !addresses.every(isPublicAddress)) throw new Error("model_destination_not_public");
}

/** The normalized base when it is usable for a participant key, otherwise null. */
export async function publicBase(
  value: unknown,
  trusted: Set<string> = new Set(),
  resolve: Resolver | null = denoResolver(),
): Promise<string | null> {
  const base = publicBaseSyntax(value, trusted);
  if (!base) return null;
  if (trusted.has(base)) return base;
  try {
    await assertPublicHost(base, resolve);
  } catch {
    return null;
  }
  return base;
}
