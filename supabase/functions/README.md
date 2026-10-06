# Edge functions

Only `leaderboard` remains: a public CORS GET wrapper around the `leaderboard()` RPC (`?phase=<slug>&limit=<n>` → `{phase, entries, data, generated_at}`).

Scoring is done by the Python worker (`worker/`) for both results files and agent packages, because the challenge v3 scorer (`challenge/scoring_core.py`) depends on the calendar, tile geometry, directional weather and request simulators; porting all of that to Deno is not worthwhile. The database trigger `score_results_webhook` is kept but disabled (`private.config.score_results_url` is empty).

Deploy: `supabase functions deploy leaderboard --no-verify-jwt --use-api`.
