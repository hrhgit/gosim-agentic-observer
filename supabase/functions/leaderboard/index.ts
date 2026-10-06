/**
 * leaderboard — Supabase Edge Function.
 *
 * GET /leaderboard?phase=<slug>&limit=<n>
 *
 * Thin CORS-enabled wrapper around the `public.leaderboard(p_phase_slug,
 * p_limit)` RPC.  The response exposes the rows under both `entries` and
 * `data` for compatibility with the Vue site, with the columns
 * rank, team_name, total_score, science_score, completion_rate,
 * uniformity_score, submission_count (plus the extra RPC columns).
 */

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY") ?? "";

const DEFAULT_LIMIT = 20;
const MAX_LIMIT = 500;

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Max-Age": "86400",
};

function json(status: number, body: unknown, extraHeaders: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...CORS_HEADERS,
      "content-type": "application/json; charset=utf-8",
      ...extraHeaders,
    },
  });
}

function parseLimit(raw: string | null): number {
  if (raw === null || raw.trim() === "") return DEFAULT_LIMIT;
  const n = Number(raw);
  if (!Number.isFinite(n)) return DEFAULT_LIMIT;
  return Math.max(1, Math.min(MAX_LIMIT, Math.trunc(n)));
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }
  if (req.method !== "GET") {
    return json(405, { error: "method not allowed" }, { Allow: "GET, OPTIONS" });
  }
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
    return json(500, { error: "SUPABASE_URL / SUPABASE_ANON_KEY are not configured" });
  }

  const url = new URL(req.url);
  const phaseParam = url.searchParams.get("phase");
  const phase = phaseParam && phaseParam.trim() ? phaseParam.trim() : null;
  const limit = parseLimit(url.searchParams.get("limit"));

  // Forward the caller's JWT when present so admins can see hidden/frozen
  // boards exactly as the RPC's security rules allow; otherwise stay anonymous.
  const authorization = req.headers.get("authorization");
  const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: authorization ? { headers: { Authorization: authorization } } : undefined,
  });

  const { data, error } = await supabase.rpc("leaderboard", { p_phase_slug: phase, p_limit: limit });
  if (error) {
    console.error(`leaderboard: rpc failed: ${error.message}`);
    return json(500, { error: error.message, phase, entries: [], data: [] });
  }

  const rows = Array.isArray(data) ? data : [];
  return json(
    200,
    {
      phase,
      limit,
      entries: rows,
      data: rows,
      generated_at: new Date().toISOString(),
    },
    { "Cache-Control": "public, max-age=15" },
  );
});
