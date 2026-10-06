# Website maintenance / 网站维护

Production: https://create.gosim.org/survey26/
Source: https://github.com/gosimfoundation/hackathon-survey26

This repository owns this event. Make future changes and pull requests here.
The former `gosimfoundation/hackathon` event directory is retired.

## Development

Use Node.js 22. Run `npm ci --prefix web` and
`npm run dev --prefix web`.

The live Survey site is in `web/` and is served at `/survey26/platform/`.
`legacy-event/` preserves the old event website, assets, database migrations,
and its Git history. The event root still redirects to the platform.
`supabase/`, `worker/`, and `scoring/` retain their existing roles. The old v3 starter kit lives at `archive/starter_kit_v3/` (still packaged into the practice download), and the retired FastAPI backend at `archive/legacy/`; `legacy-event/` still builds the event root page.
The original upstream is https://github.com/BH3GEI/agent-observer.
Future production changes must be merged into this GOSIM repository; upstream
changes do not go live until reviewed and merged here. This migration does not
change the deployed database, evaluation worker, or scoring configuration.

## Publishing

Merge into `main`. **Publish event site** builds and validates the website, then
publishes a versioned GitHub Release containing `site.tar.gz` and its SHA-256.
A failed build does not replace the previous successful release.

The shared publisher at https://github.com/gosimfoundation/hackathon polls for
successful releases on its existing five-minute schedule. GitHub may delay
scheduled runs. For an immediate refresh, run **Deploy hackathon sites to GitHub
Pages** manually in that repository after this repository's release succeeds.
No cross-repository personal access token is required.

The URL and Supabase authentication callbacks stay unchanged. Only PUBLIC
`VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` client configuration goes into
repository Actions variables. Never put a service-role/admin key there.

To reproduce a release locally, set those public variables (not needed for
Agent App), install dependencies, and run `node scripts/release-site.mjs`.
For Survey, also run `npm ci --prefix legacy-event` first.

To roll back, set this event's `release` in the shared publisher's
`config/event-sites.json` to a known-good `site-...` release tag and redeploy.
Set it back to `latest` to resume automatic updates.

## Team handoff / 切换说明

请重新克隆本仓库，并在编辑器或 AI 编程工具中打开它。旧仓库的本地副本不会
自动切换。尚未提交的修改请先保留，再迁移到本仓库；不要继续修改旧活动目录。

## Evaluation worker ownership

Website publishing and submission evaluation are separate workflows. The evaluator
in this repository is **off by default**: `EVALUATION_WORKER_ENABLED=false`.
The production evaluator remains in `BH3GEI/agent-observer`; website changes must
not silently start a second worker in this fork.

To deliberately hand over evaluation, coordinate with the existing worker owner,
configure the backend-only `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` repository
secrets, stop the previous worker, and then set `EVALUATION_WORKER_ENABLED=true`.
The public `VITE_*` frontend variables cannot replace these backend credentials.

The workflow only runs on `main` with that explicit opt-in. Missing credentials
fail a preflight check. Only a successful evaluation run may dispatch its successor;
a failed or cancelled run never starts an immediate restart loop.
