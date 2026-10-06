# Practice card γ

On top of the weather, part of your recent data can be lost once.

## At a glance

| | |
|---|---|
| Site | Paranal, Chile (virtual). Latitude −24.62°, longitude −70.40°. |
| Survey | 2026-10-09 to 2026-11-15, 38 nights. You observe when the sun is below −18°. |
| Targets | 9,900 targets on 1,980 deg² of sky, in 3 regions. 495 are required. |
| Instrument | 16 contiguous fibre assignment cells in a 4 × 4 grid. The field covers 6.4 deg² and is about 2.53° across. The fibre count and layout are those sent in `initialize`. |
| Time limit | 900 s of normalized CPU time for the whole survey (only CPU inside the agent's turns; waiting and platform processing not counted; 30-minute real-time cap). |
| Weather | Not public. During a run the agent receives a briefing every 15 minutes and a forecast about once a week. |
| Extra messages | Time-limited observation requests (`observation_request`) and their results (`observation_request_result`). Possibly one `state_resync` message. It means part of your recent data was lost. It lists the targets that still count and their best scores. Rebuild your list of finished targets from it. The time already spent is not returned. |

## Your goal

1. Observe as many valuable targets as you can, as well as you can.
2. Observe every **required** target well enough. Each one you miss costs 50 points.
3. Spread your work across the sky. Leaving parts of the sky empty costs up to 200 points.

## What your agent gets

**Once, at the start (`initialize`):**
- the site, the list of nights and when each night starts and ends;
- every target: position, class, brightness, weight and whether it is required;
- the sky regions, the instrument layout and the full score settings;
- the time limit.

**At every decision (`decision_request`):**
- the current time;
- the latest bulletin and forecast, and all messages since your last decision;
- the result of your last observation: which targets hit their fibre, and their scores;
- the time-limited observation requests in progress and how far along they are (`active_requests`);
- the time you have left.

## What your agent sends

One action per decision:

| Action | Meaning |
|---|---|
| `observe` | Point the telescope, assign targets to this card's fibres (at most one per fibre; the fibre count is in `initialize`), expose for 60–3600 s, and declare a program (DARK, BRIGHT or BACKUP). |
| `wait` | Let time pass: a number of seconds, or until a given time (for example the next night). |
| `report` | Say that the instrument is faulty now. Right: +100. After each correct report, wrong reports are free up to the card's configured allowance, then −150 each; consecutive report actions have a separate cap. |
| `finish` | End the survey now. |

## How the score works

- A target scores only if it falls in its assigned fibre's cell and stays at or above 30° altitude.
- Its score grows with brightness, exposure time and sky quality, up to a cap.
- A matching program adds 20% (DARK), 12% (BRIGHT) or 6% (BACKUP). A wrong program adds nothing.
- Only the best exposure of each target counts.
- Time-limited observation requests: complete enough of a request's targets inside its time window to earn its reward; a missed request costs nothing.
- Final score = sum of best scores − 50 × missing required targets − unevenness penalty ± reports + request rewards.

**Example.** A target has brightness 0.60 and weight 1.0. You expose it for 900 s. The sky quality is 0.75.
Its factor is 0.60 × 900 × 0.75 ÷ 450 = 0.90. You declared DARK and the sky was DARK, so the score is
1.0 × 0.90 × 1.20 = **1.08**. With a 300 s exposure the factor is only 0.30. A required target needs at
least 0.50, so it would still count as missing.

## Common mistakes

- Printing logs to stdout. Only JSON answers go to stdout. Logs go to stderr.
- Answering with the wrong `decision_sequence`, or adding unknown fields. The run stops with `agent_error`.
- Calling a language model on every decision. The time limit runs out long before the survey ends.
- Short exposures on faint targets. Exposures do not add up; only the best one counts.
- Pointing low in a direction a bulletin warns about.
- Reporting a fault after one bad exposure. Weather also lowers scores.

## Try it

Pack your whole agent project as a zip and upload it on the Participate page. Practice runs all practice cards α, β, γ and δ in the cloud, with the same 900 s limit. Daily limits are on the Rules page.

Practice scores do not decide awards.

Want to score offline first? The example projects (Resources page, "Example projects") bundle the same engine as a local runner -- see `local-cards/` and `runner/` inside.
