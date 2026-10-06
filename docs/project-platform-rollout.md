# Incremental project platform

This is the implementation contract for the new project architecture. Existing
submissions, evaluations, phase rules, boards and result files are not rewritten.
The legacy worker continues to own existing submission kinds.

## New path

1. Authenticated participants submit a repository URL or a complete ZIP. A
   repository URL may name a branch, tag, commit or project folder
   (`…/tree/<ref>/<folder>`, `…/commit/<sha>`, or the `branch`/`subdir` fields of
   the portal and both CLIs). At submission the ref is resolved to its exact commit
   and that commit's zipball (only the chosen folder, under the archive's top folder)
   is stored in `observer-sources`; preparation reads exactly that snapshot, so the
   folder becomes the project root (migration `20261004230000_repository_source_options`).
2. A GitHub App assigns a stable organization and repository to each participant
   (with team authorization recorded by the platform), snapshots the exact source
   revision, and stores private results separately from public forks.
3. A language-neutral manifest describes the container image, build arguments,
   working directory and persistent JSON-Lines command. Python is the platform
   runner implementation, not a participant language restriction.
4. Optional model-assisted adaptation only adds a reviewable adapter. Public
   scenario tests and participant approval bind the source, adapter, manifest and
   resolved image digest. Formal evaluation never silently regenerates adapters.
5. On-demand execution and the trusted simulation run on **separate machines**.
   The execution task has only public observations and a short-lived scoped run
   token. No hidden scenario files, model master keys, GitHub App private key or
   Supabase administrative credentials are present there, including at build time.
6. Local programs use the same session interface. CSV submissions in new online
   phases must match the authoritative committed trace. Legacy practice CSVs do not
   acquire this requirement retroactively.
7. Model proxy reservations, expiration, concurrency and spend checks happen in
   the trusted backend. Participant-supplied endpoints/keys remain private.
8. Only the trusted scorer publishes scores. Reports and replay are team-scoped;
   a public fork must never receive hidden scenario data or private result artifacts.
9. Design-award evidence and review remain separate from performance rankings.

## Deployment without a permanently rented server

Supabase retains identity, authorization, durable queues, run messages and quotas.
Edge functions authenticate participants and GitHub job identities, dispatch
on-demand tasks and forward model requests. The existing Python simulation runs
as a trusted, finite GitHub task, exchanging public messages through the backend;
untrusted projects run in separate tasks. This avoids rewriting the simulator in
JavaScript or placing its hidden data next to participant processes.

Queue entries are claimed atomically; dispatches and callbacks are idempotent.
Terminal runs revoke their capabilities. Infrastructure errors remain distinct
from participant errors, and retries never silently double-charge quotas.

## Acceptance and rollout

- New tables and APIs default off for existing phases. No backfill changes scores.
- Regression checks compare legacy leaderboard rows before and after migrations.
- Security tests exercise foreign-team access, invalid/expired tokens, concurrent
  model quota reservations, stale adapter confirmation and forged result callbacks.
- User-facing E2E covers ZIP/link upload, adaptation review, an actual executable
  project, local-session CSV, private results, error recovery and old practice.
- An isolated test phase and disposable repositories validate live integrations.
- Ship behind phase configuration; keep the legacy worker owning its old queue.
- PR is reviewed and tested again before merge. Rollback disables only the new
  phase path and preserves all submissions, evidence and scores.

## Work sequence

1. Validated project packages, manifests, adapter revisions and isolated runtime.
2. Additive database API, session transport and model quota proxy.
3. GitHub App ingestion, dispatch, trusted engine and executor workflows.
4. Submission, adapter review, local session, credentials and results UI.
5. Real browser E2E, integration verification, PR and post-PR verification.

## Implemented foundation and remaining integration

The source/manifest/adapter validators, isolated Docker launcher, real database
session protocol, model quota ledger, Edge session/model endpoints, and trusted
Python bridge are implemented. GitHub App operations, six-organization placement,
private source snapshots, OIDC job identity, atomic dispatch claims and retry
receipts are implemented and covered by tests. The six organizations now have
default repository permission set to none and member repository creation disabled.

The three finite Python workflow entrypoints are implemented. Preparation stores
an immutable source revision, respects an existing project manifest, or generates
a constrained model adapter, resolves the reviewed image digest, and stages a
preview project. Its receipt remains `awaiting_public_test`; it cannot approve a
revision or submit a formal score. Preparation never executes project commands.
Execution and trusted simulation use separate workflows and machines. A real
container/HTTP/PostgreSQL test exercises both handlers through download, session
exchange, private result upload, official scoring and matching local CSV. This
test uses a local job broker; it does not replace live GitHub installation testing.

`scripts/build-observer-control.py <new-directory>` exports only the trusted
Python source and pinned workflow templates. The export contains no scenarios,
participant project, credentials, legacy worker or generated caches. It never
pushes a repository. Before dispatch, the backend must approve the exact commit
produced when publishing that export in a private control repository. Workflow
requests use the [GitHub OIDC identity API](https://docs.github.com/en/actions/reference/security/oidc)
and obtain a fresh identity for a final receipt after a long run. Failed jobs stop
their own still-active run immediately without overwriting a published score.

The artifact transport supports private Git snapshots as well as exact signed PUT
staging destinations. Source, prepared project and result snapshots use separate
immutable refs; retrying cannot overwrite an earlier snapshot. The trusted job
requests a fresh, repository-scoped installation token when it stores an artifact,
so a long evaluation does not retain an expired token from startup. The executor
cannot request these credentials. An engine publishes a score only after durable
storage succeeds. The portal authorizes private downloads against the requesting
team, then signs an archive URL for the recorded commit. A real container/HTTP/Git
integration test uses a local bare repository. A live Python acceptance project
has now completed preparation, isolated execution, the authorized model call,
trusted scoring and a team-authenticated private Git result/CSV download.

The model proxy has been exercised against the organizer-authorized provider
through the real Edge API and PostgreSQL. A live model-generated adapter has also
run inside a real container: it invoked the original strategy and propagated an
original strategy exception instead of substituting an action. Model proposals
can fail (including unnecessary dependencies and incorrect initialization output);
public testing and participant review remain mandatory, not inferred from a
successful JSON response. The additive backend is now deployed for a synthetic,
team-restricted acceptance phase; existing participant phases remain disabled.

Remaining work before public launch:

- Register the unchanged formal scenario bundles and enable the actual competition
  phase after frontend deployment; preserve the existing phase dates and practice.
- Finish post-PR CI and user-facing deployment verification, then merge and publish.

Python, Rust, model-adapted and public-repository projects have now passed live
GitHub previews and private result downloads. Cloud and local two-scenario batches
have completed against the deployed backend. Initial previews exposed an executor
deadline race and Linux workspace permissions; both now have regression coverage.
The new performance board and connected participant browser journeys also pass.

The participant portal now implements repository/ZIP submission, a read-only
adapter review with explicit digest confirmation, evaluation requests, team model
provider settings, private result download, matching CSV upload, and separate
design-award evidence. Browser tests perform actual form login and those portal
operations against the real Edge code and PostgreSQL. They use a prepared revision
fixture for review; they do not prove that the GitHub preparation queue has run.
The portal is linked only when the new phase settings enable projects or local
sessions. Existing CSV routes and old tables remain intact.

The `observer-portal` function verifies the Supabase user and current team for
every request. Stored provider keys are encrypted and never returned by list APIs.
CSV acceptance hashes the uploaded bytes on the backend, ignoring any client hash.
ZIP upload retries bind to one revision. Temporary signed uploads are capped at
50 MB and five pending objects per team. Successful CSV acceptance releases its
pending-upload slot in the same transaction as recording acceptance. The timer
removes expired unsubmitted uploads and redundant temporary copies only after
the source/result has a durable private Git snapshot; it preserves upload records
and unarchived submitted originals. Existing buckets and Git history are untouched.

The optional integration test flag does not enable anything on the live site.
Old phases have no observer settings row; the new migration changes no existing
phase or score. A new batch snapshots all its scenarios. Only a fully completed
batch contributes its average to the new board; separate scenario bests cannot be
combined across different project revisions. Local runs remain pending until the
uploaded CSV matches the trusted engine's trace.

## Development checks

Use Python 3.12 with pytest, pgserver and psycopg[binary]. The HTTP tests use the
existing local harness with an actual PostgREST binary and Deno 2.9.7. No production
database is involved. Supply SAC_POSTGREST_BIN and OBSERVER_DENO_BIN, then run:

```sh
python -m pytest tests/test_project_platform.py tests/test_project_database.py -q
python -m pytest tests/test_project_jobs.py tests/test_project_repository.py tests/test_project_model_adapter.py -q
python -m pytest tests/test_project_job_worker.py -q
python -m pytest tests/test_project_portal.py tests/test_project_portal_browser.py -q
python -m pytest tests/test_project_docker.py tests/test_project_http.py -q
deno task --cwd supabase/functions check:observer
deno task --cwd supabase/functions test:observer
```

Container tests require OBSERVER_TEST_PYTHON_IMAGE and OBSERVER_TEST_RUST_IMAGE
set to pulled image digests, not tags. On macOS the pytest temporary directory
must be inside a directory shared with the local Docker VM (for example a fresh
directory under the user's cache). PostgREST may also need its libpq library
directory in DYLD_LIBRARY_PATH. CI supplies Linux containers and test tools.

The optional live model test reads OBSERVER_LIVE_MODEL_BASE,
OBSERVER_LIVE_MODEL_NAME and OBSERVER_LIVE_MODEL_KEY from process environment. Do
not place real keys in source files, fixtures or command output. It sends one
request capped at 32 completion tokens. Normal tests use a local fake provider.
The separate live-adapter test caps its generation at 4096 completion tokens and
requires an explicit pulled Python image. It checks both original-policy execution
and error propagation.

## GitHub App bootstrap and job boundaries

Run scripts/bootstrap-observer-app.py on the organizer's Mac and open its printed
localhost page in the authenticated Cindy browser. It sends the reviewed
ops/github-app-manifest.json to GitHub. If GitHub requests identity verification,
the organizer must complete that step themselves. The one-time callback stores
credentials directly in macOS Keychain under service agentic-observer26-github-app
and account BH3GEI. It never writes a PEM or credential file.

The app is public so it can be installed in six different organizations. Backend
configuration restricts use to those six competition organizations. A private
observer-control repository in each organization contains only trusted workflows.
Participant source repositories have Actions disabled. Forked public source stays
public; immutable project revisions and all detailed results use the participant's
private repository. Source snapshots cannot overwrite an existing revision.

Runner organizations 7-12 (added 2026-09-27) raise the monthly Actions budget.
The fleet is not capped at twelve: installation rows accept runner-1..99, so an
organization becomes usable the moment its row in `private.observer_installations`
is verified and enabled (13-36 are being provisioned).
A participant's organization is recorded once in `private.observer_placements`
(`public.observer_placement`): participants with existing jobs keep their
organization; new participants go to the enabled organization ranked by
`public.observer_organizations_by_load`: over the per-organization
`monthly_minute_limit` (default 1800 Actions minutes per calendar month, just
under the free allowance) last, recent dispatch failures (thirty-minute window)
demoted, then fewest active jobs, least usage over the last seven days, fewest
placed participants. When an organization-level dispatch fails (missing or
suspended installation, rejected/quota-exhausted API call, unapproved control
repository), the dispatcher moves the still-pending job and the owner's
placement to the next best organization (`public.observer_failover_job`) and
dispatches again immediately; in-flight (claimed) jobs never move.
`scripts/rebalance-observer-placements.py` (dry-run by default) moves idle
recorded placements when accumulated usage drifts apart, never targeting an
over-cap organization.

A participant's private repository stays bound to its original organization:
moving a placement creates a fresh, empty repository in the new organization
on the next job (the same GitHub App is installed everywhere, so no
re-installation or repository transfer is needed), while historical revisions
and evidence in the old organization remain readable cross-organization. The
cost of moving is therefore only repository-history continuity, not
re-provisioning; brand-new participants and failed-over jobs automatically
land on healthy organizations. A new organization stays `enabled=false` (and may be marked
`"enabled": false` in `ops/github-installations.json`) until its app installation
and control repository are verified with `configure-observer-runners.py`.

App registration and all six installations have now been verified through the
GitHub App API. Non-secret installation IDs are in `ops/github-installations.json`.
The private key is held in macOS Keychain and backend function secrets; it is not
in that file. All six private control repositories have tested and approved main
commits. Only the synthetic acceptance phase admits project submissions while
the full live end-to-end tests are in progress.

Preparation, execution, and trusted simulation receive distinct encrypted job
payloads. Claiming requires a signed GitHub OIDC token for the configured immutable
repository/organization IDs, workflow path, approved commit, main ref and dispatch
event, plus the per-dispatch random nonce. The first claimant binds the GitHub run
and attempt; another machine cannot claim a duplicate dispatch. Job completion
only writes a receipt; it cannot publish a score through the executor API.

## Model proxy contract

OPENAI_BASE_URL ends in /functions/v1/observer-model/v1; OPENAI_API_KEY is a scoped
obs_<run-id>.<capability> value, never an upstream API key. Models may use
<provider-id>::<model> to select a team provider, or just the default model name.
The initial API supports bounded, non-streaming text chat and tool-call messages.
Send a fresh UUID Idempotency-Key for each intended call and reuse it for network
retries. A duplicate returns 409 and is never forwarded again; absent an explicit
key, a new call ID is generated, so callers must not retry ambiguous failures
without retaining their own key.

The proxy also accepts the Anthropic Messages API: POST .../v1/messages, auth via a
bare `x-api-key` header (the official Anthropic SDK's own convention; a scoped
`Authorization: Bearer` also works) carrying the same obs_<run-id>.<capability>
value. ANTHROPIC_BASE_URL is injected next to OPENAI_BASE_URL -- the proxy root
*without* the /v1 segment, since the Anthropic SDK appends /v1/messages to its own
base_url itself; ANTHROPIC_API_KEY equals OPENAI_API_KEY. Only a team's own
provider (stored or relay) may use it, and only when that provider's saved
protocol is "anthropic": a mismatched route (either direction) is refused with
protocol_mismatch before any reservation, same as the OpenAI shape's own checks.
Usage settles from usage.input_tokens + usage.output_tokens.

Only exact organizer-authorized upstream bases are allowed. HTTPS is the default;
the organizer's explicitly approved HTTP test endpoint requires a separate
backend-only exception. A participant cannot authorize a new destination by
changing a request or project manifest. Credentials are AES-GCM encrypted, bound
to the provider ID, and decrypted only by the backend.

Formal runs (see `model-api-keys.md`) ignore the provider prefix: they use only
the team's saved key, or the team's open-page relay, and never organizer credits.

Reservations are atomic against both per-run and shared daily provider budgets.
Unknown usage or failed requests after forwarding consume their reserved upper
bound. Expired reservations are settled conservatively by the reconciler, which
the private dispatch timer calls while work is outstanding.

## Local complete-project sessions

The downloadable `observer-local-runner.zip` contains only Python control code
and a bilingual README. It supports Docker or explicit local-only `--native`
execution; the hosted worker never selects native execution. Any language can
supply the manifest command. Credentials are entered at a hidden prompt or read
from `OBSERVER_RUN_TOKEN`, never required in a command argument or credential file.

The backend opens a local session and records only an encrypted participant
credential for team-authorized retrieval. The engine credential is never returned
through the portal. Completed trace export contains officially committed decision
rows only, has a seven-day recovery window, and checks the official CSV digest
before creating a new output file. A cancelled/failed local runner stops its own
active run to release the paired engine; it cannot cancel a cloud run or change a
published score. Scheduling calls `observer_schedule_run`, which commits the
session, encrypted participant handoff and all jobs together. The credential uses
associated data `<run-id>:local`. The local CLI has a real HTTP/database/simulator
integration test that requests evaluation through the portal and invokes the
TypeScript scheduler over HTTP; GitHub provisioning is substituted in this test.

## Run orchestration

`observer-dispatch` first reserves eligible queued runs, provisions the owner's
private result repository, creates independent participant/engine capabilities,
and atomically stores the session and encrypted job payloads. Cloud runs require
both executor and engine jobs; local runs require only the engine. A failed
transaction leaves neither a half-open session nor a partial pair of jobs.
Reservations expire after two minutes; only their current owner may activate the
run. Duplicate acknowledgements cannot rotate capabilities or create another job.
Five failed or abandoned reservations fail the run without inventing a score.
Local scenarios start sequentially to avoid spending engine minutes waiting for
several CLI launches; finished scenarios need not wait for CSV upload before the
next starts. Cloud scenarios may run concurrently.

Hidden scenario bundles reside in the private `observer-scenarios` bucket and
their paths/digests in a private table. Only an authenticated engine job receives
a short-lived signed download URL, generated when it claims the job. Immutable
project archive URLs are also resolved at claim time, avoiding expired URLs while
waiting in the queue. Neither hidden paths nor engine capabilities are returned
to the project executor or participant portal.

## Preparation and public preview

The preparation scheduler reserves a submitted revision, provisions its private
repository, forks a public source or references an immutable private ZIP upload,
and creates a preparation job with a separate model capability. Model adaptation
is capped at one call and 65,536 reserved tokens, still subject to the organizer
provider's daily limit. A project with its own manifest does not call the model.
ZIP download URLs and repository write credentials are minted when the job claims
its work, rather than expiring while queued.

`observer_preparation_config` explicitly selects the public test scenario and the
adapter model. Its default is disabled. Weather, forecasts and events must all be
public; this is checked before scheduling and again before creating the preview.
Successful preparation freezes the materialized project but does not approve it.
The preview uses the same separate executor and engine workflows with a maximum
runtime of five minutes. A revision becomes reviewable only after the trusted
engine publishes a score and both workflows finish successfully. A failure never
becomes approval; participants can inspect/download a failed materialized adapter
and submit a corrected project. The original revision remains immutable.

Adaptation and preview batches carry a separate purpose. They neither consume
formal submission limits nor appear in the performance leaderboard or evaluation
history. Model capabilities are revoked when preparation ends. Public-test output
and downloadable prepared projects remain team-authorized private artifacts.

## Private incremental deployment and timer

`deploy-observer-backend.py --apply` records the hash of each applied Observer
migration and rejects later edits to deployed history. It compares all preexisting
public table rows before and after the update in the same transaction. Any legacy
data change aborts the deployment. Add a new migration for subsequent corrections.

`setup-observer-live-test.py` provisions a marked synthetic account, a hidden team
and a phase restricted by `access_team_id`. Existing accounts and submission rows
are preserved. Anonymous and unrelated-team queries cannot see this phase or
submit evaluations to it. Lab credentials and identifiers are kept in Keychain.

The private dispatch timer runs once a minute using existing pg_cron and pg_net.
Its narrowly scoped credential resides in Supabase Vault; the schedule contains
only a function call. It sends no HTTP request when queues are idle, and skips
overlapping ticks under a row lock. `configure-observer-dispatch.py --apply`
connects the existing Keychain capability. It does not alter the legacy weather
publication or evaluation schedules. The weather publisher continues serving
legacy CSV phases, while scenarios used by the new online phases stay private.

`observer-live-test.py` submits reproducible complete-project fixtures through the
real portal, checks status, and confirms only versions whose public test passed.
Its `--case` option keeps earlier failed attempts for audit instead of rewriting
or deleting them. It never prints signed URLs or capabilities.

At a decision deadline the executor stops the participant, then waits briefly for
the trusted engine to persist the committed prefix. It cannot submit extra actions
while waiting. Project build/program output is bounded and known credentials and
URLs are redacted before the private job receipt. The diagnostics API returns only
these fields to the owning team, never encrypted job payloads or OIDC metadata.

GitHub Linux exposed a second runtime issue: container root with capabilities
dropped cannot write a runner-owned private temporary directory. Containers now
use the workspace owner's UID/GID, preserving the existing restrictions. Each
control repository's CI verifies an actual container build in a mode-0700 directory.

`test-observer-live-browser.py` builds the frontend locally and connects it to the
deployed backend using the hidden acceptance identity. Login, private program
logs, review, public-result ZIP download, mobile Chinese layout and the existing
CSV route have passed, with zero browser script errors. This is evidence for the
connected frontend/backend journey; the new frontend is not yet deployed publicly.

The live cloud acceptance batch has completed both hidden synthetic scenarios.
Its score equals their arithmetic mean, and both private result archives contain
the canonical CSV. The two-scenario local-session batch has also completed:
both altered CSVs were rejected, both canonical exports were accepted, the batch
mean was verified and both private result archives were downloaded.

The public complete-project example is maintained at
https://github.com/BH3GEI/observer-project-example (PR #1 passed CI and was merged).
The live GitHub App has forked it into its assigned runner organization and fixed
the source commit. Its preparation, isolated public test and trusted scoring have
completed, and the owning test team downloaded the private canonical CSV.

New competition boards now read the best complete batch, including averaged score
components. Database and browser checks cover the standalone board and homepage.
Migration 015 rejects legacy submission inserts for explicitly enabled online
phases, including attempts through the old RPC. Legacy phases and stored
submissions are untouched; old workers can still finish existing submissions.

Approved control versions have immutable, content-addressed release tags.
Dispatch verifies the tag's commit and OIDC binds that exact tag, workflow and SHA.
Queue entries retain their approved version even after the control branch advances.

On macOS with Colima, container tests need a pytest --basetemp directory under
the Docker-shared home directory, such as a fresh path under ~/.cache. The default
/var/folders path is not mounted into this VM; do not relax runtime permissions to
work around that host mount setting.

## Competition activation

`configure-observer-competition.py --prepare` copies the existing formal scenario
files into immutable, hash-verified private bundles without changing public data.
`--activate --revision <published-commit>` requires that exact frontend revision
to be live, then enables project and local-session submission together. It keeps
the existing dates, scenario runtime and daily batch limit, refuses a phase with
legacy results, and does not raise the shared provider's budget. The existing
300-second public-preview limit remains in force.

The model configuration now offers organizer-approved personal providers, with
OpenRouter and DeepSeek in addition to the authorized organizer test API.
Their chat endpoints follow the official
[OpenRouter quickstart](https://openrouter.ai/docs/quickstart) and
[DeepSeek chat API](https://api-docs.deepseek.com/api/create-chat-completion/).
An organizer can add a supported HTTPS chat-completions base with
`configure-observer-secrets.py --model-base <base>`; project users cannot turn
the proxy into an arbitrary request destination. A team's model call name is
`<provider-id>::<model>`, displayed on the project page. Shared models use their
unprefixed model names. Both use the scoped runtime environment credentials.

`test-observer-live-browser.py --live-site` repeats the participant journey on the
official published site, using only the hidden acceptance account. Its default
mode still builds locally against the deployed backend.

### Large public catalogs

Initial publications over 1 MiB use a deterministic gzip/base64 envelope in the session API and database, capped at 15 MiB encoded and 96 MiB expanded with a SHA-256 integrity check. `SessionClient` transparently encodes initialization and decodes participant polls; custom session clients must support `observer-publication-gzip-v1`. The language-neutral project JSONL protocol is unchanged and receives the entire catalog, with a 128 MiB initialization limit. Ordinary decision requests retain their 16 MiB bound. No private scenario inputs enter this envelope.

The two catalog RPCs have a function-scoped 60-second SQL budget because the hosted API inherits an 8-second timeout. Catalog HTTP transfers allow 120 seconds within the existing startup deadline; ordinary decision requests retain 30 seconds and the participant decision clock is unchanged. Initialization errors stop the job with their original safe error code instead of publishing an empty score.
