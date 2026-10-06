# AGENTS.md -- typescript-pro

Guide for AI coding assistants working in this project. Humans: see README.md / README.zh.md.

## What this is

A strong TypeScript (Node.js 22+) agent for the GOSIM survey26 telescope-survey challenge
(`participant-agent-protocol-v4`). It is a module-by-module port of `../python-pro` and takes the same
decisions. Its only dependencies are `typescript` and `@types/node`, and only for the build. A deterministic
planner makes every observe decision; a model (default Kimi Coding Plan `k3`) is called twice at the start
of every night and once before any paid fault report. Read `src/agent.ts` first: it is the stdin/stdout
loop and owns pacing, fault reporting and the model stages.

## Module map (one per python-pro module)

```
src/agent.ts       agent.py        entry point: protocol loop, pacing, instrument-fault reporting, model stages wiring
src/planner.ts     planner.py      one search for pointing + fibres + duration + program; learning from results
src/skymath.ts     skymath.py      public sky maths (+ Python float semantics: %, //, round, sum)
src/advisor.ts     advisor.py      the model stages: night plan, fault review, paid-report confirmation (prompts + validation)
src/llmClient.ts   llm_client.py   OpenAI-compatible chat client (built-in fetch), calls run in the background
src/packAgent.ts   pack_agent.py   zip this folder for upload (.env, node_modules and dist are never packed)
observer.project.json, package.json, tsconfig.json, .env.example
```

## Rules for changes

- Never read card files at run time. All information comes from stdin (catalogue, public score config,
  bulletins, forecasts, your own hits).
- Never call the model per decision. The night stages run in the background; `modelWaitBudget` in
  `agent.ts` decides how long a night start may wait for them.
- Every tunable constant can be overridden with a `PRO_<NAME>` environment variable for sweeps
  (`PRO_FIXED_LEVEL=0` pins the search level, useful for reproducible local comparisons).
- Without `OPENAI_API_KEY` (or `KIMI_API_KEY`) the agent exits at start-up with
  `missing API key: set OPENAI_API_KEY`, except under `OBSERVER_MODEL_DISABLED=1` (platform evaluation without a
  model): then it runs on its rules only.
- Containers whose iteration order matters (fibre cells, picks, pending predictions) are `Map`s, because a
  plain object reorders integer-like keys. Keep it that way if you want to stay decision-identical with
  python-pro.

## Build and run

```bash
npm ci && npm run build      # tsc -> dist/ (the platform runs npm ci --include=dev, see observer.project.json)
node dist/agent.js           # what the platform runs (observer.project.json)
npm run pack                 # -> ../typescript-pro-agent.zip
```
