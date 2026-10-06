# typescript-pro -- the python-pro strategy in TypeScript

[中文说明见 README.zh.md](README.zh.md)

A TypeScript (Node.js 22+) port of [`../python-pro`](../python-pro/README.md), the strong reference agent for
the GOSIM survey26 telescope-survey challenge (`participant-agent-protocol-v4`). It has the same strategy,
the same constants and the same model stages, and on the local cards it takes the **same decisions**: with
the search level pinned and a deterministic model stub, its `decisions.csv` is byte-identical to
python-pro's. It runs on Node built-ins only (`fetch`, `zlib`, `readline`); `typescript` and `@types/node`
are needed only to build it. Like python-pro it uses only what the protocol gives an agent at run time
(catalogue, public score configuration, bulletins, forecasts and its own results) and never reads card files.

For the full design (why each part is there, and where you can still beat it) read
**[python-pro's README](../python-pro/README.md)**. This page covers the short version and what is specific
to TypeScript.

## Results (local engine, `run_local.py`)

Two comparisons with python-pro on the four local cards L1-L4 and the starter-kit demo card, using a local
stand-in for the model (an OpenAI-compatible stub that answers the three model stages by rule):

| Card | fixed effort (`PRO_FIXED_LEVEL=0`), python-pro | fixed effort, **typescript-pro** | adaptive pace + slow model, python-pro (mean ± sd, 3 runs) | adaptive pace + slow model, **typescript-pro** |
|---|---:|---:|---:|---:|
| L1 | 6,121.4 | 6,121.4 | 6,211 ± 65 | 6,123 ± 2 |
| L2 | 6,392.7 | 6,392.7 | 6,353 ± 115 | 6,387 ± 62 |
| L3 | 6,796.4 | 6,796.4 | 6,676 ± 143 | 6,781 ± 21 |
| L4 | 6,789.3 | 6,789.3 | 6,668 ± 121 | 6,773 ± 28 |
| demo | 1,717.8 | 1,717.8 | 1,717.8 ± 0 | 1,717.8 ± 0 |

- **Fixed effort** (search level pinned, stub answers at once): both agents are deterministic and their
  `decisions.csv` files are byte-identical on all five cards (two runs each).
- **Adaptive pace + slow model** (the normal clock; 30% of model answers take 20-40 s, so some arrive late):
  the realistic setting, with run-to-run noise. typescript-pro needs about 25 ms of CPU per decision against
  python-pro's about 160 ms, so on a busy machine python-pro sometimes drops to a cheaper search level and
  typescript-pro never does; that is most of python-pro's extra spread.
- **Platform** (hidden test team, practice cards, 2026-10-04): α 7,129, β 6,885, γ 6,769, δ 6,504
  (mean 6,822), every card `survey_complete` on the fair clock. The team's model key was over its quota
  during that run, so every model call failed and the rules decided (the agent's designed fallback).

## Layout (one module per python-pro module, so you can read them side by side)

```
src/agent.ts       agent.py        entry point: protocol loop, pacing, instrument-fault reporting, model stages wiring
src/planner.ts     planner.py      one search for pointing + fibres + duration + program; learning from results
src/skymath.ts     skymath.py      public sky maths: sidereal time, alt/az, gnomonic projection, fibre grid, Moon
src/advisor.ts     advisor.py      the model stages: night plan, fault review, paid-report confirmation
src/llmClient.ts   llm_client.py   OpenAI-compatible chat client (built-in fetch), calls run in the background
src/packAgent.ts   pack_agent.py   zip this folder for upload (.env is never packed)
observer.project.json   platform manifest: image node:22-slim, build `npm ci --include=dev` + `npm run build`, run `node dist/agent.js`
.env.example            copy to .env and set an API key for local runs
```

## The design in brief

- **One search per decision** for pointing, fibre assignment, exposure time and program, maximising
  `gain - lambda * T`. Candidate fields: the 12 most valuable targets placed on each of the 16 fibres, plus the
  20 densest patches of remaining science, then refined by small pointing shifts.
- **Required targets and observation requests** get probability-weighted bonuses; faint required targets
  wait for a sky close to their best and for a night that is not forecast bad.
- **Program choice from saturated hits**, which show the program multiplier exactly; the band level is
  fitted to them.
- **Instrument faults**: `E = quality level / band level` (weather lowers both, a fault only the quality).
  Free false reports are spent early, paid ones need lasting evidence. After an earthquake notice the agent
  does not probe for 12 hours, and while the earthquake's effect may last it probes only on a new step down
  in E.
- **Hidden pointing offset (Hard-mode cards)**: candidate offsets on a grid scaled to the fibre pitch
  (widened when the best one sits on its edge) are scored by which assigned targets hit or missed.
- **Fair clock**: the agent measures its own CPU time per decision (`process.cpuUsage()`), compares it with
  `wallclock.remaining_real_cpu_seconds` spread over the decisions still to come, keeps the real-time cap
  (`wall_remaining_seconds`) in view, and lowers its search level when needed.
- **Model, two stages every night plus one before a paid report**: a night plan (forecast + bulletin ->
  `bad_night`, sectors to avoid), a fault review (the agent's own hourly quality table -> `fault_likely`,
  which gates paid reports), and a confirmation that can veto a paid report. Calls start in the background
  and never block a decision: a night start waits only as long as the remaining real time allows, late
  answers are applied when they arrive, and a failure leaves the rule-based value in place. HTTP 429/5xx,
  timeouts and network errors are retried with backoff (honouring `Retry-After`).

## TypeScript notes

- **Same numbers as Python.** `src/skymath.ts` reproduces Python's float `%` and `//`, `round()` and
  `sum()` (compensated summation since Python 3.12), and the planner keeps the order of floating-point
  operations. Containers whose iteration order matters are `Map`s, because a plain object sorts
  integer-like keys. This is why the two agents agree decision for decision.
- **Async instead of threads.** python-pro runs model calls on background threads; here they are
  promises. They progress while the agent waits for the next line on stdin, so the planner never waits
  for the network. When stdin closes the process exits, even if a call is still in flight.
- **About 6x less CPU** than python-pro for the same decisions on the local cards. On the fair clock
  that leaves room for a larger search if you want to try one (`PRO_POOL`, `PRO_N_ANCHORS`, `PRO_N_DENSE`...).

- **Platform build.** The platform runs the build with the manifest's environment and a read-only home
  directory, so the manifest sets `NPM_CONFIG_CACHE=/workspace/.npm-cache` (npm's cache inside the project) and
  installs with `npm ci --include=dev` (the TypeScript compiler is a dev dependency). Manifest environment
  names must be upper case.

## Configuration (.env)

```
OPENAI_API_KEY=sk-...                            # required (KIMI_API_KEY also accepted)
OPENAI_BASE_URL=https://api.kimi.com/coding/v1   # default; outside mainland China: https://api.kimi.ai/coding/v1
OPENAI_MODEL=k3                                  # default
```

Without a key the agent exits at start-up with `missing API key: set OPENAI_API_KEY`, except under
`OBSERVER_MODEL_DISABLED=1` (set by the platform for an evaluation started with “This evaluation without a model” /
`survey26 eval start --no-model`): then it needs no key and runs on its rules only. On the platform
`OPENAI_BASE_URL` / `OPENAI_API_KEY` are injected automatically. `k3` accepts only its default temperature,
so the client sends none.

## Running locally

```bash
npm ci && npm run build
python3 ../_local/runner/run_local.py --inherit-env --card ../_local/cards/L1 --agent "node dist/agent.js" --agent-cwd .
npm run pack                                     # -> ../typescript-pro-agent.zip
```

Every constant at the top of `src/planner.ts` and `src/agent.ts` can be overridden with `PRO_<NAME>`
environment variables (for example `PRO_LAMBDA_FRAC=0.5`), exactly as in python-pro. `PRO_FIXED_LEVEL=0` pins
the search level, which makes local comparisons reproducible on a busy machine (the platform run uses the
adaptive pace).

## License

Task cards, simulated data, evaluation code and the example projects are licensed under
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/); please cite the GOSIM 2026 Agentic
Observer Hackathon (https://create.gosim.org/survey26/). See `../LICENSE.md`.
