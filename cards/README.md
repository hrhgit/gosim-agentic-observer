# Local card policy

This checkout uses only official formal competition cards or explicitly identified self-generated data for agent development and local evaluation. The bundled practice-card metadata for α–δ and local practice data for L1–L4 have been removed at the user's request. Do not download or restore them for this project's evaluations. Formal public inputs remain under `a/`, `b/`, `c/`, and `d/`; any self-generated weather, events, or requests must be stored separately.

The practice-card description below is retained as upstream documentation, not as a local evaluation workflow.

# v4 practice task cards: v4-practice-alpha, -beta, -gamma, -delta (facts only)

**Status: practice cards (Practice α, β, γ, δ).** These are debugging cards for participants,
not the hackathon cards A–D (`v4-a`..`v4-d`) or the hidden cards E–H. The organizers generate
the practice cards from secret seeds; the generator inputs are not in this repository. Once
registered, a practice card's full file set (`config/`, `public/`, `truth/`) is downloadable from
the Resources page, so participants score their agents locally with the same weather as the
platform.

## Layout

```
cards/
  v4-practice-alpha/card.json    facts of card α, read from its registered bundle
  v4-practice-beta/card.json     same for β
  v4-practice-gamma/card.json    same for γ
  v4-practice-delta/card.json    same for δ
```

`card.json` holds what a card page states (site, season, nights, targets, required targets,
regions, sky area, fibres, exposure range, score zero points, wall clock, stress) and the
manifest checksum of the bundle (`public.scenarios.checksum`). No seed and no hidden value.
`scripts/build-v4-practice-cards.py --facts BUNDLE --card <slug>` writes it from a built bundle,
`--pages` renders `web/src/content/taskcard.<alpha|beta|gamma|delta>.v4.<en|zh>.md` from it
(tests/test_v4_practice_cards.py fails if a page and its `card.json` disagree).

## Card parameters

| | v4-practice-alpha (α) | v4-practice-beta (β) | v4-practice-gamma (γ) | v4-practice-delta (δ) |
|---|---|---|---|---|
| site | Paranal -24.62 deg | same | same | same |
| footprint | 3 regions / 2000 deg2 | 3 / 1920 deg2 | 3 / 1980 deg2 | 3 / 1880 deg2 |
| targets (required) | 10000 (500) | 9600 (480) | 9900 (495) | 9400 (564) |
| nights | 2026-10-04 -> 2026-11-10 (38) | 2026-10-18 -> 2026-11-24 (38) | 2026-10-09 -> 2026-11-15 (38) | 2026-10-24 -> 2026-11-30 (38) |
| fibres / area | 16 (4x4) / 0.4 deg2 | same | same | same |
| stress | no | no | yes (data loss) | yes (data loss) |

## Registering them as practice cards

Per card: a `public.scenarios` row with the card's slug (contract `v4-score-v1`,
`global_wallclock_seconds` 900, weather, forecasts and events public), the bundle ZIP as its
evaluation bundle, `config/`, `public/` and `truth/` in the `scenarios` bucket under the slug, the
card parked in a sealed staging phase until it is linked to `practice-projects` and its files are
listed in `private.observer_scenario_public_files` (release `practice`). Do not use the slugs
`v4-a`..`v4-h`: they are the hackathon and hidden cards.

**Do not use the admin "rotate seed" action on a v4 card.** It runs
`worker/main.py:process_scenario_job`, which is v3-only and has no contract guard: it would
overwrite `scenarios/<slug>/` with a v3 scenario and flip the row's contract, while the private
v4 bundle stays untouched.

## Formal public cards in this checkout

This checkout also carries the public files for formal cards A–D under `a/`, `b/`, `c/` and
`d/`. They were synchronized from the official Resources page and contain the public configuration,
footprint, target catalogue, task-card text and night calendar. Hidden weather and truth are not included.

`formal_package_manifest.json` records the source URL, fetch timestamp, file sizes and SHA-256 checksums.
