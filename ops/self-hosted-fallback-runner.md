# Self-hosted evaluation fallback runner

## Status: switched off (2026-10-04)

Off since 2026-10-04 08:00 UTC (`fallback_capacity=0` on runner-13; the Mac's
LaunchAgents unloaded). Not deleted: the VM (`observer-fallback`, user
`observerfb`), its runner registration and every file stay, so it can come back.

Why: with 26 cloud targets (13 private organizations with health and cooldown,
`ops/private-target-health.md`, plus 13 free public repositories) a job that a
broken organization does not start already moves to a healthy one, so the
fallback no longer covers a case the cloud does not. It was used twice
(2026-10-02, before the fair clock and team egress); it was never drilled with
the current runtime, and it had real weaknesses as a last resort: one Mac on a
flaky proxy (its watcher log showed hours of TLS failures on 2026-10-03/04), an
arm64 VM unlike the x86 GitHub runners (participants' amd64-only images would
fail there; the fair clock calibrates per machine but was referenced on GitHub
runners), and a job accepted for it is never re-dispatched — it waits for the VM
until its lease ends.

The runtime it would run is always the approved tag of runner-13 (same code as
the cloud, including fair clock and team egress); the host side (Docker in the
VM, egress proxy, pf) is unchanged since 2026-10-02.

Rollback (bring it back):

```sql
update private.observer_installations set fallback_capacity=1 where organization='AGENTIC-OBSERVER26-runner-13';
```

```sh
mv ~/Library/LaunchAgents/disabled/org.agentic-observer.{fallback-watcher,egress-proxy}.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/org.agentic-observer.fallback-watcher.plist ~/Library/LaunchAgents/org.agentic-observer.egress-proxy.plist
```

GitHub removes a self-hosted runner that stays offline for 14 days (from
2026-10-04: around 2026-10-18); after that, register it again with
`ops/fallback-runner/install-runner.sh` before the rollback. Before relying on
it again, drill one job end to end with a hidden test team (a participant
image, the fair clock, team egress), and keep at least 40 GB free on the Mac.

Evaluation normally runs on GitHub-hosted runners in the runner organizations
(`AGENTIC-OBSERVER26-runner-N`). The fallback runner is an organizer-owned,
isolated Linux virtual machine that takes jobs **only when GitHub-hosted
evaluation is unavailable everywhere**. It is off unless an installation row
has `fallback_capacity > 0`.

## When a job goes to the fallback

`observer-dispatch` (`supabase/functions/_shared/observer-dispatch.ts`) and
`observer_pending_jobs` (`supabase/migrations/20261001000500_self_hosted_fallback.sql`)
route a job to the fallback only while a fallback slot is free, and only in
these cases:

| Case | Condition |
|---|---|
| Every organization fails | An organization-level dispatch failure (`ORGANIZATION_FAILURES`) on the job's organization, and the failover to the next best organization is impossible or fails the same way. |
| Runs never start | GitHub accepted three dispatches of the job, but no run claimed it ten minutes after the last one (hosted runners unavailable, Actions locked by billing or a spending limit). |
| Every organization over quota | Every enabled organization is over its `monthly_minute_limit`. If no slot is free the job still runs GitHub-hosted in its own organization. |

Transient GitHub errors (`github_unavailable`, timeouts) never move a job.
`observer_fallback_job` moves only the job to the fallback organization's
approved runtime and marks it `runner='self-hosted'`. The owner's placement,
and with it the participant repository the job reads and writes, stays where
it is (`20261002000600_job_moves_keep_repository.sql`; moving it made a moved
preparation job unclaimable). Preparation jobs never change organization at
all (`20261002000700_preparation_jobs_stay.sql`: observer-job accepts a
preparation job's repository only in the job's own organization), so the
fallback takes evaluation and score jobs; a preparation job only when the
participant is placed in the fallback organization itself. The
dispatcher then sends the extra workflow input `runner=self-hosted`, which
makes the control workflow use `runs-on: [self-hosted, linux, observer-fallback]`.
A self-hosted job already accepted by GitHub is not re-dispatched; it holds its
slot until it finishes or its lease expires.

The fallback depends on GitHub's API and Actions service just like the hosted
runners; it does not help during a GitHub-wide outage.

## On demand: the VM is stopped until a job needs it

The VM (6 GiB) is normally stopped. `ops/fallback-runner/fallback-watcher.py`,
a LaunchAgent of the organizer's account, checks every 30 seconds whether a
queued job of the fallback organization's `observer-control` waits for the
`observer-fallback` label, and if so starts the VM as `observerfb`. The
runner service starts with the VM and picks the job up; the watcher stops the
VM after 15 minutes without waiting jobs while the runner is idle, and boots it
once a week while unused (GitHub removes self-hosted runners that are offline
for 14 days, and the runner updates itself while online).

- **What it reads, what it holds.** Only GitHub job and runner metadata of that
  one repository, through the organizer's existing `gh` login. No database
  credential, no participant code, no job payload. It acts on the VM only
  through a sudo rule that lets the organizer run `limactl` as `observerfb`.
- **Boot latency.** A stopped VM is ready and its runner online about a minute
  or two after the job is queued (longer when the runner has to update
  itself). `observer_fallback_job` therefore gives a moved job a fresh
  thirty-minute claim window and shifts its run's session by the time the job
  had already waited, so the team keeps its full evaluation window. If the
  runner is still offline ten minutes after a start, the watcher restarts the
  VM.
- **A persistent registration, not an ephemeral runner.** The runner stays
  registered while the VM is stopped (GitHub shows it offline and queues the
  job for it). An ephemeral runner would need a fresh registration token for
  every job, i.e. a GitHub credential that can administer the repository in a
  long-running process on the Mac, and a reinstall on every boot. Isolation
  between jobs does not depend on it: every job starts and ends with
  `job-cleanup.sh`, and each VM runs one job at a time.
- **Stopping.** The watcher stops the VM only after re-checking that no job
  is waiting and the runner is not busy; any error while checking keeps the
  VM running. A job assigned in the second or two between that re-check and
  the shutdown is cancelled by GitHub; its platform job then expires and the
  run fails as an infrastructure failure (never a score). The maintenance
  boot happens at most once a day, so a runner that never comes online does
  not keep the VM cycling.
- The VM disk is sparse (40 GiB at most); keep that much free space on the Mac.
- **Network through the Mac's proxy.** The runner and Docker
  reach GitHub and registries through the egress proxy, which chains to the
  local proxy and falls back to a direct connection when that proxy fails.
  Python 3.12 for `actions/setup-python` is installed once at setup, and job
  cleanup keeps registry images (content-addressed) so jobs do not re-pull
  them; it removes every image built in the VM.
- **A job whose run dies before it reports** stays claimed until its lease
  expires (as on GitHub-hosted runners) and holds its fallback slot until then.

## Security boundary

Participant code must never reach the organizer's Mac. Four layers, from the
inside out:

1. **Job sandbox** — as on GitHub-hosted runners, participant code runs only in
   the trusted job runner's Docker container (`--cap-drop=ALL`,
   `no-new-privileges`, read-only root, non-root user, resource limits).
2. **gVisor** — in the VM, Docker's default runtime is `runsc`: a container
   talks to gVisor's user-space kernel, so a kernel exploit does not reach the
   VM kernel. (amd64-only images are not supported on the fallback.)
3. **The VM** (`ops/fallback-runner/observer-fallback.yaml`) — Lima `plain`
   mode: no host directory mounts, no port forwarding, no guest agent, no SSH
   agent forwarding. Nothing from the Mac (browser profile, GitHub login,
   keychain, `~/.ssh`) exists inside; the only secret is the runner's own
   registration, created from a one-hour register-only token. The runner is
   registered at **repository level** on `observer-control`, so only the
   reviewed control workflows run there. Every job starts and ends with
   `/opt/observer/job-cleanup.sh` (all containers, images, volumes and
   temporary files removed), so one VM hosts exactly one runner. An in-VM
   egress guard (iptables `OUTPUT`, `DOCKER-USER`, `INPUT`) keeps containers
   and non-root code away from the Mac (`192.168.5.2`), private, fake-IP and
   link-local networks and from the VM itself; the runner service requires it.
4. **Host boundary (required before enabling)** — Lima's user-mode network
   maps `192.168.5.2` to the Mac's loopback, and a local HTTP proxy happily
   forwards to `127.0.0.1` too. Code that becomes root inside the VM can undo
   the in-VM guard, so the Mac must enforce the boundary itself:
   - the VM runs under a dedicated macOS account (`observerfb`), and pf
     (`ops/fallback-runner/pf.anchor`) lets that account reach only the public
     internet, the VM's own SSH port and the egress proxy — never the Mac's
     other loopback services (e.g. the browser's debugging port) or the LAN;
   - `ops/fallback-runner/egress-proxy.py`, run by the organizer's account,
     accepts only `CONNECT <allowlisted host>:443` (GitHub, container
     registries, PyPI, gVisor, Supabase) and chains to the local proxy.
     IP literals, loopback names and other ports are refused.

## One-time setup (organizer's Mac)

Prerequisites: Lima (`brew install lima`), the organizer's `gh` login. No
Docker on the Mac.

1. **Administrator part, once** (asks for the password once; idempotent):

   ```sh
   bash ops/fallback-runner/setup-host.sh
   ```

   It creates the hidden standard account `observerfb` that only runs the VM,
   installs the pf anchor (`ops/fallback-runner/pf.anchor`, loaded now and at
   boot by `org.agentic-observer.pf.plist`) and a sudoers rule
   (`/etc/sudoers.d/observer-fallback`) that lets the organizer run `limactl`,
   and nothing else, as `observerfb` without a password. Undo commands are at
   the top of the script.

2. **VM, checks, runner, watcher** (no password):

   ```sh
   bash ops/fallback-runner/setup-vm.sh
   ```

   It copies the proxy, watcher and VM definition to
   `/Users/Shared/observer-fallback` (organizer-owned; `observerfb` can read,
   not write), starts the egress proxy LaunchAgent (`127.0.0.1:18080`,
   upstream `127.0.0.1:1082`), checks the pinned Ubuntu image (downloaded
   unless `IMAGE=` points to a copy, for example from a mirror; the SHA-256
   must match the digest in `observer-fallback.yaml`), creates and boots the
   VM as `observerfb` with `HostProxyPort=18080`, public `Resolvers` (the
   Mac's resolver may answer with a local proxy's fake IPs, which the VM
   refuses) and an `AptMirror`, then checks every layer against a
   loopback-only canary service on the Mac:

   | Check | Expected |
   |---|---|
   | host files inside the VM | none |
   | Docker's default runtime | `runsc` |
   | a container connecting to the Mac | blocked |
   | VM root with the in-VM guard bypassed connecting to the Mac | blocked (pf) |
   | VM root asking the egress proxy for the Mac's loopback | refused |
   | the runner account reaching GitHub through the proxy | 200 |

   Only if every check passes it registers the runner on
   `AGENTIC-OBSERVER26-runner-13/observer-control` (`install-runner.sh`; the
   one-hour register-only token goes over stdin), waits until GitHub shows it
   online, stops the VM and starts the watcher
   (`org.agentic-observer.fallback-watcher.plist`, log in
   `/Users/Shared/observer-fallback/watcher.log`).

`setup-vm.sh` pauses the watcher while it runs. The VM is disposable: to re-register, delete it
(`sudo -u observerfb -H limactl delete -f observer-fallback`), remove the
runner under the repository's Actions → Runners and run `setup-vm.sh` again.

## Rollout checklist (production)

1. Merge this change; apply migration `20261001000500_self_hosted_fallback.sql`.
   It adds columns with safe defaults (`fallback_capacity=0`,
   `runner='github-hosted'`), so nothing changes yet.
2. Deploy the `observer-dispatch` edge function.
3. Republish the fallback organization's `observer-control` from this main
   commit (`scripts/build-observer-control.py`), tag
   `observer-runtime-<sha>`, and approve that sha in its installation row. The
   new workflows accept the optional `runner` input; other organizations may
   keep their current runtime because the input is only sent to the fallback.
4. Set up the host boundary and the VM and register the runner (above);
   the runner is listed (offline while the VM is stopped) under the
   repository's Actions → Runners and the watcher log shows it watching.
5. Enable: `update private.observer_installations set fallback_capacity=1
   where organization='AGENTIC-OBSERVER26-runner-13';`
   `fallback_capacity` is the number of fallback VMs registered there; each
   VM hosts exactly one runner because its job cleanup removes every
   container. A split (execute + engine) run needs two free slots, so phases
   that are not colocated need two VMs to use the fallback.

Disable at any time with `fallback_capacity=0` (pending self-hosted jobs keep
their slot until they finish or expire); the watcher stops the VM once it is
idle. To stop watching altogether:
`launchctl bootout gui/$(id -u)/org.agentic-observer.fallback-watcher`.

## Production rollout record (2026-10-02)

- Host setup on the organizer's Mac: `setup-host.sh` (one administrator
  password), pf anchor reloaded twice for the SSH listener fix (#161);
  `setup-vm.sh` passed all six isolation checks; runner `observer-fallback-1`
  registered on `AGENTIC-OBSERVER26-runner-13/observer-control`; watcher loaded.
- runner-13 runtime `0794cb2` (exported from main `9a924e9`) approved;
  migrations `20261001000500`, `20261002000600`, `20261002000700` applied;
  `observer-dispatch` with the fallback routing deployed (v13 from main
  `9a924e9`; v15 is the later public-runner-pool deploy).
- Drill with the hidden acceptance team, practice card "v4 public test": the
  public-test engine job, dispatched GitHub-hosted to runner-1, was moved with
  `observer_fallback_job` (the call the dispatcher makes). The runner-1 run
  could no longer claim it; the self-hosted dispatch went out at 09:07:01 UTC,
  the watcher started the stopped VM at 09:07:39, `observer-fallback-1`
  claimed the job at 09:08:26 and the run was scored at 09:09:05; the VM
  stopped again after 15 idle minutes.
- `fallback_capacity=1` on runner-13.

Earlier drill attempts found the problems fixed in #161 and #163: the SSH
listener's replies blocked by pf, the system libpython breaking setup-python,
image pulls through the Mac's proxy, and preparation jobs that cannot change
organization.
