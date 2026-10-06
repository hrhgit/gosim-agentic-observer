# Fair clock (organizer notes)

Code: `challenge/fair_clock.py` (used by `challenge/v4_workflow.py`, the platform engine and both
local runners). Participant-facing rule: rules §5 item 10, docs "Time limit", FAQ.

## Rule (default mode `cpu`)

- Budget per card: 900 normalized CPU seconds (`runtime_seconds` of the phase, capped by the card).
- Charged: CPU time of all the agent's processes and threads **inside its turns** (from sending a
  `decision_request` until the response arrives), read from the container's cgroup
  (`cpu.stat usage_usec`, v1 `cpuacct.usage` as fallback), at most the turn's length, divided by
  the speed factor. Engine time, waiting (model API, network, idle) and CPU steal are free.
- Speed factor: median time of a fixed single-thread Python workload (`workload()`), measured by
  the engine on the same machine before the agent starts (5 repetitions) and about every 60 s
  between turns with the container frozen (`docker pause`; 3 repetitions); factor = median of the
  last 5 samples / `REFERENCE_UNIT_SECONDS` (0.25 s).
- The budget is also enforced inside a turn (the engine polls the meter every 0.2 s and kills an
  agent that has spent it). Hard real-time cap from the first request: 1800 s (2 x budget).
- Each request: `wallclock.{elapsed_seconds, remaining_seconds}` (normalized CPU budget),
  `remaining_real_cpu_seconds` (remaining x factor), `speed_factor`, `cpu_seconds`,
  `wait_seconds`, `wall_remaining_seconds`, `clock_mode`. `initialize.limits` carries `clock` and
  the first `speed_factor`.
- Fallback mode `charged_wait`: charged = cpu / factor + (turn - cpu); hard cap 2700 s.

Configuration (repository variables of each runner repository, passed to the engine workflow):
`OBSERVER_CLOCK_MODE` = `cpu` | `charged_wait` (empty = `cpu`), `OBSERVER_WALL_CAP_SECONDS`
(60-21600, empty = mode default). The session deadline (migration
`20261004060000_fair_clock_session_deadline.sql`) is 3 x runtime + 120 s for colocated phases,
so caps above 2820 s are cut by the session.

## Measurements (2026-10-04, GitHub-hosted ubuntu-24.04 runners)

Lab: `AGENTIC-OBSERVER26-runner-11/fairclock-lab` (public, so no paid minutes); the agent runs in
Docker with production flags pinned to 2 vCPUs; the compute-adaptive reference agent ("pro").

- Calibration unit on 32 runners: median 0.253 s, range 0.12-0.30 s (2.4x between AMD EPYC 9V45
  and EPYC 7763). Within a run, single repetitions spike up to 1.9x, hence medians.
- CPU / wall inside turns: 0.997 for a compute-bound agent; steal ticks 0. Engine time between
  turns about 10 s per run (3-5 %).
- Model API latency from 12 runners (TLS + one request): api.kimi.com median 0.43-0.53 s per
  runner (first call median 0.66 s, worst 1.3 s); api.moonshot.cn 0.73-1.22 s.
- Score vs machine speed, card L4 with a 150 s budget (time pressure), slope = score change for a
  2x slower machine:

| Clock | runs | sd | r(score, log unit time) | slope |
|---|---:|---:|---:|---:|
| before (real time, engine included) | 24 | 8.0 % | -0.64 | -13.4 % |
| normalized think time, remaining in this machine's seconds | 20 | 7.3 % | -0.23 | -5.2 % |
| cpu/factor + wait, remaining in charged seconds | 16 | 11.2 %* | -0.08 | -4.0 % |
| cpu/factor only (default), agent unchanged | 16 | 5.0 % | -0.75 | -11.0 % |
| cpu/factor only (default), agent paces on `remaining_real_cpu_seconds` | 16 | 5.0 % | -0.22 | -3.6 % |

  (* one 3,197 outlier from the agent's own bimodal behaviour.) With the default mode the
  remaining budget is fair, but an agent that compares `remaining_seconds` with its own
  real-time measurements under-uses the budget on slow machines and over-uses it on fast ones;
  `remaining_real_cpu_seconds` (or dividing own CPU time by `speed_factor`) removes that.
- Card L4 with the full 900 s budget (agent far from the limit): before sd 2.1 % (n 6), default
  mode sd 1.0 % (n 6).

## Capacity

Recent runs (3 days): engine run time p50 89 s, p90 414 s, p99 908 s; only 1.4 % ended on the
clock. With the default mode a run takes about factor x CPU used + waiting + ~10 s, at most the
30-minute cap. Hidden final: about 78 teams x 4 cards x 3 runs = 936 runs; `run-hidden-final.py`
schedules 5 runs per minute, so scheduling takes about 3.1 h and the cap adds at most 33 minutes
at the end; even if every run hit the cap, at most about 165 runs run at once (26 targets x 20
concurrent jobs = 520 slots).
