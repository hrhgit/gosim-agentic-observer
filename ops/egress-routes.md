# Egress routes (出网线路)

Some model relays refuse requests from GitHub-hosted runner addresses. A team may
therefore send its evaluation traffic through an organizer-provided route:

| Team setting (「密钥与网络」 → 出网线路, `survey26 env route`) | Path |
|---|---|
| `direct` 直连 (default) | the runner's own address, as before |
| `cn` 回国代理 | the China node |
| `overseas` 海外代理 | one of the overseas nodes, chosen at random per run; the others are the failover |

`auto_fallback` (default on): when no node of the route works, a connection goes
direct; off: it is refused. Only labels are stored or shown anywhere.

## Where the node settings live

Only in the owner's vault (`BH3GEI/ops-vault`, `survey26/secrets/egress-proxies.txt`)
and in the Supabase secret **`OBSERVER_EGRESS_ROUTES`**: JSON
`{"cn": [outbound], "overseas": [outbound, ...]}`, each a sing-box outbound
without `tag` (`vless` with REALITY, `hysteria2`; at most 1 China and 4 overseas
nodes). They never reach the database, a job input, the website, an RPC response,
a workflow input or log, an artifact or the participant container.

1. The scheduler stores the route **label** in the (encrypted) job input:
   `team_egress.route = {name, fallback, cap_bytes}` (`observer_run_team_egress`).
2. At claim time the job API (`observer-job`) adds `nodes` from the secret. For the
   public repositories the whole claim payload is sealed to the job's in-memory
   runner key (observer-seal-v1); for the private organizations it travels in the
   authenticated TLS claim, as the team's own secrets do.
3. The runner (`project_platform/team_egress.py`) starts the pinned route client
   (`ROUTE_IMAGE`, sing-box 1.14.2 by digest) with `--network container:<sidecar>`,
   so it shares only the sidecar's network namespace; the configuration goes to its
   standard input (`run -c stdin`), never a file, argument, environment variable or
   log (`log.disabled`, container `--log-driver none`, output to /dev/null). It
   listens on the sidecar's loopback only (one SOCKS port per node), unreachable from
   the participant's network. The sidecar script itself knows only the local ports
   and the node labels.
4. The sidecar keeps every existing protection: it resolves and checks each
   destination (public addresses only, no rebinding) and hands the route client the
   **checked IP address**, never the name, so the far end cannot be pointed at an
   internal address of the node either.

## Health, failover, cap

* At start the sidecar checks every node at once (a SOCKS connection to fixed public
  probes) and starts with the first healthy node in its random order.
* A failed connection triggers a probe of that node: a node that still reaches the
  probes stays (the destination was the problem); otherwise the next node is used
  from then on. With every node down the route is retried after 60 s.
* Proxied bytes (both ways) per run are capped at
  `observer_hardening.egress_route_cap_bytes` (default 500 MB). Over the cap,
  proxied connections are closed and new connections go direct (fallback on) or are
  refused (fallback off).
* Concurrency is already bounded by the per-team evaluation limits.

## Records

* The team's run log (`agent.log`), `egress.json` and `observer_run_egress_log`
  show the path of every destination: `direct`, `cn`, `overseas:node1..4` (labels
  in the secret's order), plus a route summary (proxied bytes, node switches,
  direct fallbacks). No node address appears.
* Organizers: `private.observer_run_egress.path` and
  `private.observer_run_egress_route` (one row per job: route, fallback, cap,
  proxied bytes, capped, failovers, fallbacks, bytes per path label).

```sql
-- usage per node label, last day
select p.key as path, sum((p.value->>'bytes_up')::bigint+(p.value->>'bytes_down')::bigint) bytes,
       sum((p.value->>'connections')::bigint) connections
  from private.observer_run_egress_route r, jsonb_each(r.paths) p
 where r.recorded_at>now()-interval '1 day' group by 1 order by 2 desc;
select route, count(*) runs, sum(failovers) failovers, sum(fallbacks) fallbacks, count(*) filter (where capped) capped
  from private.observer_run_egress_route where recorded_at>now()-interval '1 day' group by 1;
```

## Switches

```sql
-- staged: test/pilot teams first (also needs open egress)
update private.observer_hardening set egress_routes_teams=array['<team uuid>']::uuid[] where id;
-- everyone
update private.observer_hardening set egress_routes=true where id;
-- cap per run (bytes, both ways)
update private.observer_hardening set egress_route_cap_bytes=524288000 where id;
-- rollback (one line): every run goes direct again; teams' choices are kept
update private.observer_hardening set egress_routes=false, egress_routes_teams='{}' where id;
```

Rotating or replacing nodes: update the vault, regenerate the JSON and set the
secret (`supabase secrets set OBSERVER_EGRESS_ROUTES=...` or the Management API),
then redeploy nothing: `observer-job` reads it at start (a new isolate picks it up
within minutes; redeploy `observer-job` for an immediate switch). Keep the node
order stable so the labels (`overseas:node2`) keep meaning the same node.

## Deployment record (2026-10-04)

* Migration `20261005020000_egress_routes` applied and registered (14:19 UTC);
  `observer-job`, `observer-dispatch`, `observer-portal`, `survey26-cli` deployed;
  secret `OBSERVER_EGRESS_ROUTES` set (1 China node, 3 overseas nodes).
* Runtime: public repositories `266caa0` on all 13 (previous `6adf43a`); private
  organizations approved from main `7c22cd9` (#301), since superseded by #313
  (which contains it).
* Pilot (hidden test team, `egress_routes_teams`): 6 evaluations × 4 practice cards
  with a probe agent (real model calls) — China route on a private organization
  (runner-5) and in the public repositories, overseas route on both, plus a direct
  baseline: 23 of 24 runs scored and verified by the rescore (one public run lost
  its job API call with HTTP 401 at the end; the repeat batch scored 4/4). Exit
  address: China route CN (Shenzhen), overseas route US, direct Microsoft/Azure;
  model call latency to api.kimi.com ≈ 2.7–3.5 s (China), 1.1–1.6 s (overseas),
  1.2–1.5 s (direct). Overseas runs used node1, node2 and node3 (random per run).
* Leak check: every public run log of the 13 public repositories since the
  approval (312 runs, 2,756 files), the team's result files, run logs, egress log,
  workspace and environment responses, the organizers' tables and the private
  organization run logs (370 sources): no node host, address, uuid, password,
  public key or short id; the port number only inside unrelated timestamps and
  decimals. No artifacts. Inside the container: no route client process or setting,
  the SOCKS ports unreachable, CONNECT to loopback/other ports refused, no UDP.
  The check found two older places that showed the China node's host name (it is
  also the organizers' former model relay): the workspace list (`model_bases`,
  shared provider row; fixed in #314) and `scripts/configure-observer-secrets.py`
  (#315). Git history still contains the host name; only the host, no credential.
* Global switch on 15:31 UTC (`egress_routes=true`).
  Rollback: `update private.observer_hardening set egress_routes=false, egress_routes_teams='{}' where id;`
