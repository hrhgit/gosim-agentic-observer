# Participant keys, network access and model APIs

## Egress route (出网线路, optional)

With open egress, a team may choose an egress route (`direct` default, `cn`, `overseas`;
migration `20261005020000_egress_routes`, switch `observer_hardening.egress_routes`).
The sidecar then sends every connection it allows through the pinned route client in
its own network namespace, with failover between nodes, an optional direct fallback,
a per-run cap on proxied bytes and the path of every destination in the egress log.
Node settings exist only as the Supabase secret `OBSERVER_EGRESS_ROUTES` and in the
claim payload; see `ops/egress-routes.md`.

## Evaluations without a model and switched-off variables

Migration `20261005071000_no_model_evaluations`.

* **本次不提供模型 / This evaluation without a model** (website checkbox next to 评测 / 评测 3 次取平均;
  `survey26 eval start|selfcheck --no-model`; portal `evaluate` with `no_model: true`,
  `observer_create_batch(..., p_no_model)`, `observer_create_repeat_batches(..., p_no_model)`): the batch gets
  `model_disabled = true`. For its runs `observer_run_team_egress` leaves out every variable tagged `model`, so
  those values never leave the database for that run, and returns `model_disabled: true`; the scheduler puts
  `model_disabled: true` into the engine (colocated) or execute job input and the runtime sets
  `OBSERVER_MODEL_DISABLED=1` (and drops the model-proxy settings). The network policy is unchanged. The retired
  model proxy refuses such runs (`observer_model_route`: `model_disabled`). Only project evaluations in
  non-sealed phases; the hidden final (`observer_run_hidden_final`) never sets it. The flag is shown as 「无模型」
  in the evaluation records and in `evaluation.json` of the combined result download.
* **The `model` tag**: set automatically for `OPENAI_*`, `ANTHROPIC_*`, `*_API_KEY`, `*_BASE_URL`, `*_MODEL`,
  `*_MODEL_NAME`, `*_PROTOCOL` and `*LLM_*` names and for variables saved by 「添加模型服务」; teams change it
  in 「密钥与网络」 or with `survey26 env tag NAME model|none` (`observer_set_team_variable_flags`).
* **Switched-off variables** (`disabled`, 「停用」, `survey26 env disable|enable NAME`): kept (secrets stay
  encrypted) but given to no run and no preparation job.
* Statistics: `private.stats_llm_usage_daily.model_disabled` (part of the key) for paired comparisons.

## Open egress and the egress log (current)

With `observer_hardening.open_egress` on (or the team in `open_egress_teams`;
migration `20261004120000_open_egress_and_egress_log`), the team's domain list is
not used. The scheduler sends `team_egress: {environment, secrets, domains: [],
open: true}` and the runner's sidecar (`project_platform/team_egress.py`, open
mode) lets the participant container reach **any public destination on port 443
or 80**:

- HTTPS `CONNECT host:443|80` on port 3128 (`HTTPS_PROXY`/`https_proxy` are set;
  `HTTP_PROXY` is not, so plain `http://` goes the transparent way);
- transparent: the sidecar is the container's DNS server (`--dns`) and answers
  every A query with its own address (AAAA: empty), then splices TLS on 443 by the
  ClientHello's server name and HTTP on 80 by the `Host` header.

Other ports are refused (only 443 and 80 are needed for HTTP APIs; add one to
`OPEN_PORTS` if a real need appears). The sidecar resolves every destination
itself and refuses it unless **every** address is public (`ipaddress.is_global`,
not multicast; IPv4-mapped IPv6 unwrapped): private, loopback, link-local
(169.254/16, so every cloud metadata address), CGNAT (100.64/10), benchmarking and
documentation ranges are never reached, also when a public name resolves or
rebinds to them. A public IP literal is allowed through CONNECT; the container has
no route of its own, so direct IP connections without the proxy fail.

**Egress log.** The sidecar keeps, per destination host and port, connections,
refusals (blocked or unreachable), bytes up/down and first/last time (UTC), at
most 500 destinations (more go to `(other)`), never any content, in
`/tmp/egress.json` inside the sidecar. The runner reads it before removing the
sidecar and:

- appends a table to the team's run log (`agent.log`, "Network connections during
  this run") and writes `egress.json` into the run's result bundle (engine jobs);
- puts it into the job receipt (`result.egress`, also for failed runs); a trigger
  on `private.observer_jobs` copies it into `private.observer_run_egress`
  (organizers: `select * from private.observer_run_egress where run_id=...`);
  teams: `observer_run_egress_log(p_run)` for their own runs.

**Rollout / rollback.** Pilot: `update private.observer_hardening set
open_egress_teams=array['<team>']::uuid[] where id;` Global:
`update private.observer_hardening set open_egress=true where id;` Rollback (one
line): `update private.observer_hardening set open_egress=false, open_egress_teams='{}' where id;`
New runs then use each team's domain list again (allow-list mode below). The
workspace hides the domain list while open egress is on.

Automatic adaptation (preparation) with open egress accepts any public https base
name (no IP literal, internal name or other port) for `*_BASE_URL`.

## Keys and network (team egress, allow-list mode)

With the organizer switch `observer_hardening.team_egress` on, the platform no
longer relays model traffic for project runs. Each team saves, in the workspace
section **Keys and network**:

- **Variables** (`private.observer_team_variables`): up to 20 `NAME=value` pairs,
  `NAME` matching `[A-Z][A-Z0-9_]{0,63}`, at most 8 KB each. Names starting with
  `OBSERVER_` or `SAC_`, ending in `_PROXY`, and `PATH`, `HOME`, `HOSTNAME`,
  `LD_PRELOAD`, `LD_LIBRARY_PATH`, `PYTHONPATH`, `NODE_OPTIONS` are reserved.
  Secret values are encrypted by the portal Edge function (AES-GCM bound to the
  variable's id) and shown only by their last 4 characters; plain values (base
  URLs, model names) are stored and shown as they are. Secret values are deleted on
  the saved-key schedule (`observer_key_purge_after`, hourly cron
  `observer-purge-team-variables`).
- **Allowed domains** (`private.observer_team_domains`): up to 10 public DNS
  names. The portal refuses IP literals, reserved/internal names and any name that
  does not resolve only to public addresses.

The scheduler (`observer-orchestrate.ts`) reads `observer_run_team_egress`,
decrypts the secret values in memory and puts
`team_egress: {environment, secrets, domains}` into the encrypted input of the
engine job (colocated runs) or the execute job (split runs, e.g. the hidden final).
`observer-job` validates it again at claim time. The runner
(`project_platform/team_egress.py`):

1. builds the project exactly as before (the build keeps registry access);
2. starts the pinned forwarder sidecar on a per-run internal Docker network,
   dual-homed onto the default bridge, with public resolvers (1.1.1.1, 8.8.8.8);
3. starts the participant container on the internal network only, with the
   team's variables as its environment (they override the manifest's
   `environment`), `--add-host <domain>:<sidecar>` for every allowed domain, and
   `HTTPS_PROXY`/`HTTP_PROXY` (both spellings), `NO_PROXY=""` and
   `NODE_USE_ENV_PROXY=1`.

The sidecar accepts HTTPS `CONNECT host:443` (port 3128) and direct TLS on port
443, where it reads the ClientHello's server name. Either way it allows only an
allowed domain on port 443, resolves it itself, refuses it unless every address
is public (no private, loopback, link-local, CGNAT, benchmarking or metadata
range, also for IPv4-mapped IPv6), and connects only to those checked addresses,
so DNS rebinding cannot reach an internal service. TLS is end to end; the sidecar
never sees plaintext, keys or responses. Secret values are redacted from the
private run logs. The participant container still gets `OBSERVER_API_URL`,
`OBSERVER_RUN_TOKEN` and `OBSERVER_RUN_ID`, and no platform model credential.

Several providers, protocols and models work at the same time: one key per
provider, one domain each. Example (Python, official SDKs, `KIMI_API_KEY` saved
and `api.kimi.com` allowed; Kimi speaks both the OpenAI and the Anthropic shape):

```python
import asyncio, os
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

openai_style = AsyncOpenAI(base_url="https://api.kimi.com/coding/v1", api_key=os.environ["KIMI_API_KEY"])
anthropic_style = AsyncAnthropic(base_url="https://api.kimi.com/coding", api_key=os.environ["KIMI_API_KEY"])

async def ask_both(question: str):
    fast, careful = await asyncio.gather(
        openai_style.chat.completions.create(
            model="kimi-for-coding", max_tokens=256, messages=[{"role": "user", "content": question}]),
        anthropic_style.messages.create(
            model="k3", max_tokens=1024, messages=[{"role": "user", "content": question}]),
    )
    return fast.choices[0].message.content, careful.content[0].text
```

Google Gemini works the same way through its OpenAI-compatible endpoint
(`https://generativelanguage.googleapis.com/v1beta/openai/`, domain
`generativelanguage.googleapis.com`); any other HTTPS API (e.g. a decision API
called with plain HTTP requests) only needs its key saved and its domain allowed.

**Migration.** `20261004040000_team_variables_and_egress` turns each team's saved
model API into variables (`OPENAI_*` or `ANTHROPIC_*` by its protocol: `_API_KEY`
reusing the ciphertext, whose provider id becomes the variable id, plus
`_BASE_URL` and `_MODEL`) and allows the base's host. Teams that used the page
relay have no stored key; the workspace asks them to enter it
(`relay_key_missing`). Re-running `private.observer_backfill_team_variables()`
fills only teams without variables.

**Rollout and rollback.** Apply the migration, deploy `observer-job`,
`observer-portal` and `observer-dispatch`, publish the runtime to every control
repository (and the public pool) and approve it, then
`update private.observer_hardening set team_egress=true`. Turning it off again
restores the previous behaviour for newly scheduled runs (model proxy, Model API
section); a runtime without team egress support simply ignores the field.

**Retiring the model proxy (migration `20261004080000`).** Since team egress is on
(2026-10-04 05:11 UTC) no evaluation calls the proxy. The two remaining users moved:

- Model-assisted preparation (projects without `observer.project.json`): with
  `prepare_direct_model` on, the prepare job gets the team's variables
  (`team_egress`) instead of a proxy credential, and the runner calls
  `OPENAI_*` (chat completions) or `ANTHROPIC_*` (Messages) directly. The base
  must be https on one of the team's allowed domains, and `*_MODEL` must be set.
- Local runner: without `--model-base-url` it gives the project the participant's
  own `OPENAI_*` / `ANTHROPIC_*` variables (and `--env-file NAME=value` lines).

`model_proxy_retired` then makes the proxy refuse (`model_proxy_retired`, HTTP 410)
every run of a team that has team egress on. The code stays deployed, and turning
team egress off (its rollback) reopens the proxy for those teams immediately, so
the proxy is still the egress rollback path. The organizer-credit route ("Organizer
test API", qwen; last call 2026-09-24 09:51 UTC, 8 calls) is disabled.

```sql
select public.observer_set_model_proxy(p_prepare_direct=>true);   -- stage 1
select public.observer_set_model_proxy(p_retired=>true);          -- stage 2
select public.observer_set_model_proxy(p_prepare_direct=>false, p_retired=>false);  -- rollback
update private.observer_providers set enabled=true where team_id is null;          -- organizer route back
```

**Live 2026-10-04.** Runtime with direct access published and approved on all 13
`observer-control` repositories (main `3a68787`, previous shas in git history of
`ops/github-installations.json`). Hidden test team "Proxy retirement test (hidden)"
(Kimi via `OPENAI_*`, domain `api.kimi.com`): a project without
`observer.project.json` was adapted by calling Kimi directly, public test passed,
zero proxy calls. With the proxy retired and direct access off, the same project's
preparation failed (proxy refused) and was requeued; once direct access was on, the
retry passed. Both switches on since 08:30 UTC.

---

# Model proxy (previous path; retired for team-egress teams, rollback only)

Formal runs never use organizer model credits. "Formal" means every run for which
`private.observer_personal_models_only()` is true: runs in a phase that counts for
the final ranking or has the slug `online`, and every run (including model-assisted
project preparation) while the site is in competition mode. Other runs, such as
the practice/acceptance paths outside competition mode, keep their existing
behaviour. Deterministic projects need no API.

Each team chooses in the workspace section **Model API (optional)**:

| | Save encrypted (default) | Do not save (page relay) |
|---|---|---|
| Key location | AES-GCM ciphertext in `private.observer_providers` | only the open page's memory |
| Page must stay open | no | yes, until each evaluation finishes; also at the time agreed for top-team verification |
| Deleted | automatically after the results are verified (see "Automatic deletion"), or by the team at any time | when the page closes |
| Call path | model proxy calls the provider directly | model proxy → Broadcast → open page → portal → provider |
| Per-run bounds | phase settings: 100,000 calls, up to 4 at a time; tokens 1,000,000,000 (effectively uncapped) | phase settings: 100,000 calls, up to 4 at a time; tokens not metered |
| Size caps | 64 KiB request, 192 KiB response | 64 KiB request, 192 KiB response |

Saving on the server is the default: a team that has not explicitly chosen is in
stored mode (`20261002170000_stored_model_mode_default`, on top of the opt-in
table and function from `20260926000600_model_key_opt_in_auto_purge`; a team that
already explicitly chose relay, or chose stored or saved a key, keeps that
choice — "not chosen" means no row at all in `private.observer_team_model_modes`).
The reason: with the page relay, the page must stay open during evaluations and
contestants kept forgetting. The workspace lists "Save encrypted on the server
(default)" first and preselected, with "Do not save (page relay)" second and one
sentence on the trade-off (e.g. "Saved (default): stored encrypted and deleted
automatically after the results are verified. Not saved: keep this page open
during evaluations."). Choosing "Save encrypted on the server"
(`observer_set_team_model_mode('stored')`) or saving a key selects stored mode.
Choosing "Do not save" deletes a saved key immediately
(`observer_set_team_model_mode('relay')`, same transaction). Only team members
(not banned) can change the choice or manage the key; it is team-wide.

## Stored mode (default)

1. A member enters any public HTTPS endpoint (see "Which endpoints are accepted"
   below), the model and the key, and saves (`save_team_model` portal action,
   over HTTPS). The portal checks the endpoint, creates a new provider ID and
   encrypts the key with the Edge
   application key `OBSERVER_KEY_ENCRYPTION_KEY` (AES-GCM, provider ID as
   additional data). Only the ciphertext and, for keys of at least 16 characters,
   the last four characters as a recognition hint reach the database
   (`observer_save_team_model`, service role only). One key per team; saving
   again replaces it and wipes the previous ciphertext.
2. Clients only ever receive `{mode, saved: {base_url, model, key_hint, saved_at}}`.
   No API returns the key or ciphertext.
3. The project keeps using the scoped `OPENAI_BASE_URL`/`OPENAI_API_KEY` (or, for
   an anthropic-protocol provider, `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`) of
   its run. `observer_model_route` answers `{personal: true, mode: "stored",
   protocol: "openai"|"anthropic"}` and the
   proxy reserves the call with `observer_reserve_team_model`. It checks the run
   capability and deadline, the per-run call/token limits and at most
   `model_concurrency` outstanding calls, and returns only the run team's own
   saved provider. Without a saved key the call fails with `team_model_not_configured` (HTTP 403). No organizer,
   shared, legacy or other-team provider is ever substituted; the
   `a_disallow_stored_formal_model` trigger on `private.observer_model_calls`
   enforces this again for every call receipt.
4. The Edge function re-checks the base with the endpoint rule below (including
   a fresh DNS lookup), decrypts the key in request memory only,
   sends one non-streaming request with `redirect: "error"` using the model the
   project named in this request (see "Per-call model choice" below), redacts the key from the
   response and drops its reference. Usage reported by the provider is settled;
   unknown usage is charged at the reserved upper bound.
5. Provider bodies, headers, redirects and exception text are never forwarded;
   the project receives only an error code.

The key never enters project containers, job payloads or Actions inputs (projects
receive only their scoped run credential), run artifacts, logs, audit-log details
(team, user and base URL only) or error messages.

`observer_delete_team_model` (and choosing relay mode) wipes the key at once. A
provider row referenced by call receipts keeps only non-secret metadata (base URL,
model name) with an empty `encrypted_key`; unreferenced rows are deleted. Calls
already in flight may finish. A team that saved a key but never made a call must
delete the key before it can be disbanded (the row references the team).

## Relay mode ("Do not save")

Unchanged ephemeral flow. The participant selects a supported HTTPS base, model
and key in the workspace; they stay in Vue memory without browser storage. Each
active run gets an unguessable Broadcast channel, readable only by the owning
team. The proxy reserves a call ID and broadcasts the prompt; the open page
submits it with the key to the authenticated portal, which verifies team
ownership and the exact prompt digest, claims the call once and calls the
page's endpoint only if it passes the endpoint rule below, with redirects disabled. The database stores the channel
and call receipts, never the key, ciphertext, prompt or response. Without an
attached page the call fails within the bounded wait; there is no organizer
fallback. The relay functions refuse teams in stored mode
(`personal_model_not_enabled`; `observer_personal_model_routes` returns no
routes). Do not use SQL `realtime.send()` for this relay: database Broadcast
persists messages (https://supabase.com/docs/guides/realtime/broadcast).
The relay uses the run's `model_call_limit` and `model_concurrency` (a call
counts as outstanding for at most 150 seconds); tokens are not metered.

If a relay-mode team is verified as a top team after the competition, its page
must be open at the time agreed with the organizers so the re-run can call its
model.

## Per-call model choice

The model saved in the workspace is the team's **default model**. For the team's
own endpoint and key (stored or relayed) the proxy forwards the `model` of each
request unchanged, so an agent can use a fast model for some steps and a stronger
one for others; the team's key pays. The default model is used when a request
has no `model`, an empty one, or the alias `team-model` (platform preparation
sends this alias). A `model` that is not a string of 1-256 characters without
control characters is refused with `invalid_model` (HTTP 400) before anything is
reserved. The relay page forwards the agent's body unchanged and sends its own
model field only as the default, so the call digest recorded by the proxy and the
digest the page claims are computed over the same body.

When the provider rejects the agent's model as unknown (HTTP 400/404/422 whose
body mentions the model together with "not found", "does not exist", "invalid",
"unsupported", "不存在" and similar), the proxy retries once with the default model
and returns that result. Nothing else is retried, and the default model itself is
never retried. Both attempts belong to one call: one reservation, one digest/claim,
one settlement (the successful attempt's usage), one shared deadline. The provider's
error body is inspected in memory only and never forwarded. Relay mode does the
same inside the portal.

This applies to every team endpoint, including those listed in
`OBSERVER_MODEL_BASES` (shown as suggestions): they are always used with the team's
own key. Organizer-credit calls (non-formal runs, the organizer provider) are
unchanged: the model must be on that provider's own `models` list in the database.

## Protocol: OpenAI-compatible or Anthropic Messages

Each team also chooses which shape its provider speaks, default
**OpenAI-compatible** (`POST .../v1/chat/completions`). Opting into **Anthropic
Messages** (`POST .../v1/messages`) lets a project call Claude (or any
Anthropic-Messages-compatible provider) directly with the official Anthropic
SDK. The choice is a `protocol` column on the team's model settings
(`private.observer_team_model_modes`, default `'openai'`), set together with
the saved key (`save_team_model`) or independently for relay mode
(`set_team_model_mode`), and shown by `observer_team_model()`. The platform
never translates between the two shapes: a `/v1/messages` call against an
openai-protocol provider (or a `/v1/chat/completions` call against an
anthropic-protocol provider) is refused with `protocol_mismatch` before any
reservation. Only a team's own provider (stored or relay) may use Anthropic
Messages; organizer-credit practice runs stay OpenAI-compatible only
(`protocol_not_supported`).

Otherwise identical to the OpenAI path (capability check, deadline,
call/concurrency reservation, size caps, key redaction, error sanitizing):

- **Auth header**: `x-api-key: <key>` — the official Anthropic SDK's own
  convention, a bare key with no `Bearer` scheme. A client that instead sends
  `Authorization: Bearer <key>` with the same scoped credential is also
  accepted. A client-supplied `anthropic-version` (default `2023-06-01`) and
  `anthropic-beta` are forwarded unchanged.
- **Upstream URL**: `<base>/v1/messages`, or `<base>/messages` when the saved
  base already ends in `/v1` — the same "/v1-or-not" normalization the OpenAI
  path uses, matching how the official Anthropic SDK itself appends
  `/v1/messages` to its own `base_url` (which, unlike `OPENAI_BASE_URL`, does
  not include `/v1`).
- **Request body**: the Messages shape — no `system` role in `messages` (a
  top-level `system` string or text blocks instead); `max_tokens` is required;
  `tool_use`/`tool_result` content blocks are allowed; image/document blocks
  are refused, for the same bounded-accounting reason the OpenAI path refuses
  image URLs. Streaming is refused the same way.
- **Usage settlement**: `usage.input_tokens + usage.output_tokens` (instead of
  `usage.total_tokens`).
- **Container env**: `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` (the same
  scoped run credential as `OPENAI_API_KEY`) are injected next to the
  `OPENAI_*` pair, in every runner (cloud jobs, the local runner, native mode).
  `ANTHROPIC_BASE_URL` is the proxy root *without* the `/v1` segment, because
  the official Anthropic SDK appends `/v1/messages` to its own `base_url`
  itself. With restricted egress, both base URLs point at the one run-scoped
  sidecar prefix; the sidecar forwards `x-api-key`/`anthropic-version`/
  `anthropic-beta` next to the existing `Authorization`/`Idempotency-Key`.

The workspace's **Model API** section has a protocol select next to the
endpoint/model/key fields, in both modes.

## Organizer steps

Deploy in this order: `scripts/deploy-observer-backend.py --apply` (migrations
`20260926000200_stored_model_keys` and `20260926000600_model_key_opt_in_auto_purge`;
the script applies every migration from `20260925000100` onward and records each
hash), then the Edge functions `observer-model` and `observer-portal`, then the
website. Formal model calls fail closed in between.

`OBSERVER_MODEL_BASES` (exact bases, comma-separated, written by
`scripts/configure-observer-secrets.py`) is no longer an allowlist for teams. Its
HTTPS entries are shown as suggestions in the workspace and are trusted as they
are (the local test stacks rely on this for their loopback stubs). Its HTTP
entries are never usable for team keys.

## Which endpoints are accepted

Teams are not limited to a provider list. The same rule applies to saved keys
(when saving and again before every call) and to the page relay
(`_shared/observer-public-base.ts`):

- `https://` only, default port 443, no user name or password, no query or
  fragment; a trailing slash is dropped.
- The host must be a DNS name with at least one dot. IP literals (in any
  spelling) are refused, as are `localhost` and names under `.localhost`,
  `.local`, `.internal`, `.intranet`, `.lan`, `.home.arpa`, `.arpa`, `.test`,
  `.example`, `.invalid` and `.onion`.
- The name is resolved (A and AAAA) and every address must be public unicast.
  Refused: 0/8, 10/8, 100.64/10, 127/8, 169.254/16, 172.16/12, 192.0.0/24,
  192.0.2/24, 192.88.99/24, 192.168/16, 198.18/15, 198.51.100/24,
  203.0.113/24, 224/4 and above, and every IPv6 address outside 2000::/3 or in
  2001:db8::/32, 2001::/23 or 2002::/16 (this covers ::1, IPv4-mapped, NAT64,
  ULA fc00::/7, link-local and multicast). A name without any record is
  refused. If the runtime offers no working DNS lookup, the naming rules still
  apply.
- Redirects are never followed, and TLS certificate checks bind each connection
  to the name. A name that later resolves to an internal address therefore
  still cannot reach an internal HTTPS service, and internal plain-HTTP services
  are out of reach because HTTP is refused.

A refused endpoint returns `model_destination_not_enabled` when saving, and
`provider_not_authorized` for a call.

Phases whose runs use the team's own key (formal `online` and final phases,
`practice-projects`, internal `observer-acceptance-*`) get these per-run model
limits in `public.observer_phase_settings`: `model_call_limit` 100,000,
`model_token_limit` 1,000,000,000 (effectively uncapped; the team pays for its
tokens) and `model_concurrency` 4. Every call still passes through the
`observer-model` Edge function, so calls stay capped. Migration
`20260926000700_participant_model_limits.sql` sets them for existing phases;
`configure-observer-competition.py`, `configure-observer-practice-projects.py`
and `configure-observer-acceptance.py` use them for new ones. Phases that use
organizer keys are unchanged. Runs opened earlier keep the limits they started
with.

### Automatic deletion

No organizer step is needed. Where pg_cron is available (the hosted database),
the job `observer-purge-provider-keys` runs `private.observer_auto_purge_provider_keys()`
every hour (at minute 17). It wipes every participant-owned key of a team
(saved team keys and keys retained from the former provider settings) with the
same `private.observer_forget_provider_key` as the manual purge, once **all** of
these hold:

1. **No phase that can use the key is open or upcoming.** The phases that can
   use a team's key are the active phases whose runs are formal
   (`private.observer_personal_models_only`): `counts_for_final`, slug `online`,
   `observer-acceptance-*` and `practice-projects`; while the site is in
   competition mode, also every other active phase with
   `observer_phase_settings`. A phase restricted to another team
   (`access_team_id`) is ignored. Every such phase must have an `ends_at`, and
   the latest one must have ended at least the retention period ago. An
   open-ended phase (`ends_at` null) keeps the key.
2. **The key was saved at least the retention period ago.**
3. **Nothing of the team is in progress:** no queued/starting/ready/running run,
   no queued/running batch, no queued/preparing project revision and no
   unsettled model-call reservation on its keys.

Why this rule: the schema has no "results verified" flag, so the end of the last
phase that can use the key plus a retention period (default **7 days**) stands in
for the verification window, including re-runs of top teams. Condition 3 means a
key is never deleted under a running or queued evaluation. The check takes the
team row lock, as saving and deleting a key do.

The team's mode is left as it is; the workspace then shows stored mode without a
saved key, and the team can save again or choose "Do not save". Each run that
deletes keys writes an `observer.provider_keys_auto_purged` audit entry with the
count and team IDs (never key material).

Settings (service role), in `private.observer_key_retention`:

```sql
update private.observer_key_retention set retention = interval '14 days' where id;  -- longer verification
update private.observer_key_retention set enabled = false where id;                  -- pause
select * from private.observer_key_retention;  -- last_run_at shows the last check
```

Without pg_cron (plain PostgreSQL, e.g. the tests) the function is installed but
not scheduled; call `select private.observer_auto_purge_provider_keys();` from
any scheduler.

### Manual purge

To delete every participant key at once, regardless of phases, run with the
service role, for example in the SQL editor or management SQL API:

```sql
select public.observer_purge_provider_keys();  -- returns the number of keys deleted
select count(*) from private.observer_providers
  where team_id is not null and encrypted_key <> '';  -- expect 0
```

or `POST /rest/v1/rpc/observer_purge_provider_keys` with the service-role key.
It wipes every participant-owned key (saved team keys and keys retained from the
former provider settings), removes the saved-key records and writes an
`observer.provider_keys_purged` audit entry with the count. Organizer providers
(`team_id is null`) are untouched and call receipts remain. It is idempotent and
returns 0 when nothing is left. Unlike the automatic deletion it does not wait
for evaluations in progress (calls already in flight may finish).

## Validation

- `tests/test_personal_models.py` (real PostgreSQL): stored is the default for a
  team that never chose, an explicit relay choice is never overridden, and the
  opt-in migration keeps teams that saved (and is idempotent), save, replace,
  delete, team isolation, no key or ciphertext in any participant-visible result,
  stored-mode reservations use only the team's own key, no organizer fallback in
  either mode, choosing relay deletes the key, relay claims and receipts,
  `model_concurrency` outstanding calls and per-run limits in both modes, manual
  purge, automatic deletion (open, upcoming and recently ended phases, retention
  after saving, other-team and competition-mode phases, queued runs and unsettled
  calls keep the key; pause switch; audit), and the migrations' limit updates.
- `web/tests/modelKeyMode.test.ts`: the workspace defaults to "Save encrypted on
  the server", an explicit relay choice still reads as relay, and every locale
  explains the trade-off.
- `observer-model_test.ts`, `observer-portal_test.ts`: HTTPS/approved-host and
  redirect rules, decryption per request, redaction, response cap, no fallback,
  encryption before storage. `observer-personal-model_test.ts`: relay behaviour.
- `tests/test_project_http.py`: real Deno Edge functions, PostgREST and an HTTPS
  stub provider trusted through a throwaway test CA: a formal run calls the saved
  provider with the saved key and model, redirects and provider errors are not
  forwarded, deleting the key stops use, relay mode fails closed without any
  server-side call, and the plaintext key appears in no table or Edge log.
- `tests/test_project_portal_browser.py`: default stored mode with the trade-off
  sentence, save with masked hint, switch to relay (key deleted), relay key not
  kept in browser storage, opt in again, headings in all four languages.
- Opt-in: `integration/deployed-personal-model.ts` (selects relay mode first,
  which deletes that team's saved key) and `integration/personal-broadcast_test.ts`.
- Anthropic Messages: `observer-model_test.ts` (`x-api-key`/`Authorization`
  fallback, the Messages body bounds, the saved-mode call and its usage
  settlement, size caps, redaction, timeouts), `observer-personal-model_test.ts`
  (relay fulfilment with `x-api-key` and the Messages shape),
  `tests/test_project_http.py` (real Deno Edge functions and PostgREST: a
  formal run calls the saved anthropic-protocol provider with `x-api-key` and
  no `Bearer`, and an OpenAI-shaped call against it is refused with
  `protocol_mismatch` before any reservation, and vice versa), and
  `tests/test_evaluation_hardening.py` (`ANTHROPIC_BASE_URL` derivation, the
  Docker runtime's env allow-list, and the restricted-egress switch mirrored
  onto `ANTHROPIC_BASE_URL`).

A live check with a real provider through the deployed proxy is still required
before relying on either mode; these tests use stubs and synthetic keys.
