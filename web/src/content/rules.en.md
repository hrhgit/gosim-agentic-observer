## 1. Eligibility and teams

1. Participation is open worldwide to individuals and teams. One account per person.
2. A team has 1 to 3 members. A person belongs to at most one team. Submissions are made on behalf of a team.
3. Organizers, evaluation-platform maintainers, and their immediate collaborators are excluded from awards. Their teams are marked hidden on the boards.
4. Team names and content must follow the code of conduct (section 8).

## 2. Schedule and task cards

All participants use the same Participate page; the platform selects the active competition. The event uses three sets of task cards. Each card is one complete simulated survey season:

| Task cards | Used for | Published |
|---|---|---|
| Practice cards α, β, γ, δ | Practice | Now: the card pages and public inputs (targets, sky footprint, site, telescope and fibres, observing calendar, score settings); weather, forecasts and events stay private |
| Hackathon cards A, B, C, D | Online competition evaluations and the online board | When the competition starts, Oct 5, 00:00 Beijing time (Oct 4, 16:00 UTC; Oct 4, 09:00 PDT; Oct 4, 12:00 EDT): the card pages and the public inputs; weather, forecasts and events stay private |
| Hidden cards E, F, G, H | The final evaluation after the deadline, which decides the final ranking | Not before the hackathon ends; participants do not see them during the event |

The fourth card of each set (δ, D, H) is an extreme card: less observable time and more disruptions. Card pages are on [Task cards](/cards); downloads are on [Resources](/resources).

| Stage | Time (UTC+8) | What happens |
|---|---|---|
| Practice | Open until the competition starts | Submit a complete project on the practice cards for cloud evaluation; boards are for practice only |
| Online training | Oct 2–3 | Introduces the simulator and participant protocol; details in announcements |
| Competition | Oct 5 00:00 – Oct 7 23:59 | Submit complete projects; every evaluation runs once on each of the cards A–D; the online board updates live; choose your final version |
| Hidden-card final evaluation | After Oct 7 23:59 | Organizers evaluate each team's final version 3 times on the hidden cards E–H and average each card |
| Verification and results | After the hidden evaluation completes; expected before Oct 10 | Organizers verify the top teams, then publish the final standings (hidden-card scores only) |
| Awards Day | Oct 17 | GOSIM Shenzhen conference (top three teams invited) |

### Practice rules

1. Download the example project, use its local practice cards to run your agent locally and check its score, then pack the agent and submit it as a complete project (public GitHub repository or ZIP) on Participate: the Practice board. The platform runs it round by round in the cloud (same evaluation flow as the competition). Every evaluation runs once on each practice card, with a runtime limit of 900 seconds per card.
2. The daily number of evaluations is the quota shown on Participate, reset at 00:00 UTC (08:00 Beijing time); evaluations that fail because of the platform are not counted. Model calls may only use the team's own model API key.
3. Practice ranks each card separately and each team keeps its best score per card; exact ties favor the earlier submission. Practice scores do not decide awards.
4. Any language or algorithm is allowed, and practice runs need no model call. Note that awards require agent (LLM-driven) techniques in at least two of these stages: natural-language understanding, data parsing, task planning, action decisions, tool calling and plan adaptation (section 3, item 6). It is worth preparing for this now.
5. Kimi Coding Plan: the first 100 teams that run through Practice (at least one successful score: a scored complete-project evaluation) each receive one Kimi Coding Plan code, first come, first served, while codes last. The captain claims it on the dashboard once organizers release the codes; every team member can see it there. Use endpoint `https://api.kimi.com/coding/v1` and model `kimi-for-coding` or `k3`. Hidden and test teams are not eligible.

## 3. What you submit

1. Submit a complete project from a public GitHub repository (the default branch, or a branch, tag, commit or project folder you name) or a private ZIP up to 50 MB. Any language is allowed. Review and confirm the fixed source revision, launch settings and proposed adapter after the platform checks them.
2. Formal competition accepts complete projects only. CSV files and local CSV sessions are not accepted.
3. Each evaluation receives current observations one round at a time: the server records each decision before releasing the next round. Future weather, events and instrument faults remain private.
4. Every team has the same daily quota. One evaluation runs once on each of the cards A, B, C and D, with a runtime limit of 900 seconds per card. The number of evaluations per team per day is the quota shown on Participate, reset at 00:00 UTC (08:00 Beijing time). Within that quota you may evaluate freely; a team can run up to 4 evaluations at a time (an “Evaluate 3 times and average” set counts as one, and its 3 evaluations run one after another). Evaluations that fail because of the platform do not count toward the quota; failures caused by the project itself (build failure, crash, output that violates the protocol) do. Participate also offers an “Evaluate 3 times and average” self-check: the same version is evaluated 3 times in a row, using 3 of the day's evaluations, and the record shows the mean per card and overall and the range (lowest–highest) across the 3 evaluations. Each of the 3 evaluations counts as an ordinary evaluation on the online board; their mean is not shown on the board.
5. **Final version.** During the competition, any team member can mark one of the team's confirmed (and not withdrawn) versions as the team's final version on the Participate page, and change or clear that choice until the competition ends (Oct 7 23:59 UTC+8). The choice is then locked. If a team never chooses, the version of its best online evaluation is used. A chosen final version cannot be withdrawn.
6. **Agent technology requirement.** To qualify, a project must use agent technology (driven by a large language model) in at least two of these stages: natural-language understanding, data parsing, task planning, action decision-making, tool calling, and plan adaptation. The organizers decide this mainly from an analysis of the final version's code by Claude. The platform does not require a model call in every evaluation round (section 4), but projects that do not meet this requirement are not eligible for awards.

## 4. Execution and model APIs

1. Python is the platform runner, not a language restriction. The confirmed project runs in its specified container.
2. Model calls are optional. Participants provide their own API and quota; organizer model credits are not provided. Any provider, protocol and model may be used, several at the same time. In the "Keys and network" section of Participate, save the variables your program needs (API keys, endpoints, model names); during evaluation they are provided to the program as environment variables. During evaluation the program can reach any public address over HTTPS (port 443) and HTTP (port 80); other ports, private and internal addresses, the local host and cloud metadata addresses are unreachable. The platform records every address the program connects to (host and port, number of connections, bytes sent and received, first and last time), never any content; the record is attached to your team's run log and result files, and the organizers can see it as well. Keys are stored encrypted on the server, used only for evaluation and verification, shown only by their last 4 characters, can be replaced or deleted at any time, and are deleted automatically after the hidden final results are published and verified. No page needs to stay open during evaluation. Do not place keys in project sources.
3. External compute and external services are allowed, including your own model APIs and servers. The platform container limits (2 CPU cores, 2 GB memory) apply only to the part that runs on the platform.
4. Every decision must be made automatically by your program. Human participation in, or substitution for, decisions is prohibited; external services may be used, but not to bring a human into the decisions.
5. Attempts to read other teams' data, tamper with scoring or deliberately exhaust platform resources can lead to disqualification.

For model calls, save your keys and other variables in "Keys and network", for example `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`. Your program calls the provider directly with a common SDK, chooses the model for each request, and may call several providers at the same time (for example a fast, inexpensive model for simple steps and a stronger one for key decisions). The platform sets `HTTPS_PROXY`, so common SDKs need no extra configuration. Model calls are paid for by your team.

## 5. Scoring

Each card is scored by the published scorer with the parameters in that card's `config/v4_score_config.json`; the `initialize` message carries them in full. The values below are those of the current cards; the full protocol and formulas are listed below and on the [task card](/cards) pages.

1. **Survey and actions.** Each card is one survey season; observing is possible while the sun is below −18°. At every decision the agent sends one action: `observe` (point the telescope, put targets on fibres, expose for 60–3600 s and declare a program: DARK, BRIGHT or BACKUP), `wait`, `report` (the instrument is faulty now) or `finish`. The number of fibres and their layout follow each card's configuration (`config/v4_fiber_config.json`, also sent in `initialize`); always use the values your agent receives at run time. The current practice cards α–δ each have 16 fibres (4×4).
2. **Target score.** A target scores only if it falls in its assigned fibre's cell and stays at or above 30° altitude for the whole exposure. Factor = min(brightness × exposure seconds × sky quality ÷ (f0 × T0), 1), where f0 × T0 comes from the card's score settings (`flux_zero_point` × `exposure_zero_point_seconds`; 0.5 × 900 = 450 on most cards); target score = science weight × factor × program bonus.
3. **Program bonus.** When the declared program matches the target's sky band, the score is multiplied by DARK 1.20, BRIGHT 1.12 or BACKUP 1.06; otherwise by 1.00.
4. **Best exposure only.** A target may be observed again, but exposures do not add up: only its best score counts.
5. **Required targets.** Every required target that is not completed costs 50 points; completion needs a best factor of at least 0.5, and the program bonus cannot replace that threshold.
6. **Even coverage.** The completed fraction is computed per 10° right-ascension band and its evenness is measured with Jain's index J: the penalty is 200 × (1 − J), at most 200 points.
7. **Fault reports.** Instrument faults never appear in bulletins or forecasts; only exposure scores reveal them. A `report` while an unrepaired fault exists earns +100 and repairs it at once. After each correct report, wrong reports are free up to the card's configured allowance, then −150 each; consecutive report actions have a separate cap.
8. **Time-limited observation requests (R_request).** During the survey a card may publish time-limited observation requests on schedule (`observation_request` messages in `new_messages`; the decision snapshot's `active_requests` shows live progress). A request lists a set of targets, a minimum number to complete, a completion threshold, a reward and a deadline; valid exposures that lie wholly between its publication and its deadline count automatically, with no accept action. Reaching the minimum (judged on the completion factor, without the program bonus) earns the request's reward at the deadline; a missed request costs nothing. An `observation_request_result` is sent at the deadline, and re-sent as a correction (`revised: true`) if a data loss later voids exposures.
9. **Total** = sum of best target scores − 50 × missing required targets − evenness penalty ± fault reports + R_request (the rewards of completed observation requests).
10. **Time limit (timing rule).** Each card has a budget of 900 seconds of **normalized CPU time**. Only the CPU time the agent actually uses during its own turns counts: a turn runs from each `decision_request` the platform sends until the response arrives, and the CPU time is read by the platform from the agent container's system accounting, covering all processes and threads. That CPU time is divided by the machine's speed factor. The speed factor is measured by the platform on the same evaluation machine with a fixed calibration program (once before the card and about every 60 seconds during it, with the agent paused); the median evaluation machine has factor 1.0, and a slower machine has a larger factor. Waiting (for example on a model API or the network) and the platform's simulation and processing are not counted. Every request carries `wallclock.remaining_seconds`, the budget left, together with `speed_factor`, the cumulative `cpu_seconds` and `wait_seconds`, and `wall_remaining_seconds` until the real-time cap; plan your computation by `remaining_seconds`, and divide CPU time you measure yourself by `speed_factor` to put it in the same unit. To stop programs that hang and to bound evaluation time, each card also has a real-time cap of 30 minutes, which ends the survey as well. The survey ends when the budget or the real time runs out, when the agent errs (`agent_error`) or when it sends `finish`; earlier observations still score and the terminal penalties still apply. This timing applies to every evaluation started from 4 October 2026 (Practice, the online competition and the hidden cards); results of evaluations completed before then remain valid.
11. **Hackathon cards A–D.** The four cards are fixed and identical for every team, with no per-team randomization. When the competition starts, their card pages and public inputs (targets, sky footprint, site, telescope and fibres, observing calendar, score settings) are published; weather, forecasts, events and instrument faults are not, and during an evaluation the agent only receives bulletins and forecasts step by step. The platform records each decision before releasing the next round; future rounds cannot be submitted early and accepted decisions cannot be replaced. The result ZIP of each of your own evaluations remains downloadable.
12. **Evaluation score and online board.** An evaluation's score is the arithmetic mean of its four card scores (A, B, C, D); it ranks on the online board only when all four cards are complete. The board keeps each team's best evaluation, without combining card maxima from different evaluations, and also shows each card's score.
13. **Hidden cards E–H.** After the competition ends, organizers evaluate each team's final version (section 3, item 5) 3 times on each of four hidden cards E, F, G and H that no participant has seen, also 900 seconds per card and outside the daily quota. Each card's score is the mean of its 3 evaluations; the final score is the arithmetic mean of the four card scores; the online board does not decide the final ranking. In the final, the 3 evaluations of each card run at the same time, so make sure your model service can handle concurrent calls (a team's 3 evaluations share its model key and rate limits). The hidden cards, their per-step data, run logs and results stay private until the organizers publish the final results. Averaging several evaluations reduces the effect of a program's own run-to-run variation on the ranking. We recommend making your agent behave consistently on the same input, for example by making your program's own random number generation repeatable and avoiding strategies that depend on execution speed or machine performance; during the competition you can check its stability with “Evaluate 3 times and average” (section 3, item 4).

## 6. Ranking, ties, and verification

1. **Final ranking**: only the hidden-card score (E–H) of each team's final version (section 5, item 13) counts, by score, descending. A hidden card not completed in one evaluation because of the team itself (for example a crash, a timeout or a rejected trace) scores 0 in that evaluation and counts in that card's mean; a failure caused by the platform never counts as 0 and is re-evaluated by the organizers: so that each card's 3 evaluations always run at the same time, all 3 evaluations of the team are run again together and the earlier results are discarded (kept for the record). Ties on the hidden score are settled by the organizers and announced with the results.
2. **Online board**: during the competition it ranks each team's best evaluation (section 5, item 12), live; on an exact tie the earlier evaluation ranks first. It is for feedback only and does not decide awards. Organizers may freeze it during the final hours.
3. The organizers run the hidden-card final evaluation after the online phase freezes, using the variables each team saved in "Keys and network" and the same network rules as the online phase; no page needs to be open. Teams whose program calls a large model should make sure, before the competition (the online phase) ends, that the keys they need are saved. Teams that do not use a model are unaffected. Saved keys are deleted automatically after the hidden final results are verified and published.
4. Before awards are confirmed, organizers verify the top teams: they may re-run the final version, and may ask for the complete code, the versions and configuration of the external services and models used, and the call records from evaluation. Keep those external services and your saved keys available until verification ends. Results that cannot run, that are clearly inconsistent with the ranked score, or that involved human intervention are removed.
5. Organizers may re-score submissions if a scorer defect is found. Any change to the scorer or its parameters is announced with a version number and applies to every submission of the phase.

## 7. Awards

| Award | Prize | Count |
|---|---|---|
| First Prize | $2,000 | 1 |
| Second Prize | $1,000 | 2 |
| Third Prize | $500 | 3 |

Amounts are gross. The first- and second-prize teams are invited to the GOSIM Shenzhen conference on October 16–17, 2026, and go on stage to receive their awards at the closing ceremony on October 17. In principle, GOSIM covers their travel and accommodation. Each team gets a booth and a roll-up banner on site to present itself and its project to conference attendees. GOSIM conference staff will contact the winners with details after the competition. Attendance is not required to receive a prize.

**Open source.** Award-winning entries must be released as open source; we recommend a license such as Apache or MIT.

Design evaluation is separate from the performance board. It considers code, run
records and reproducibility, not explanation length. Participants can save
architecture and reproduction notes on the Participate page. Design-award places and
prizes will be announced separately.

## 8. Code of conduct

1. Be respectful to one another. Harassment, discrimination, and abusive content in team names, notes, agent output, or posts and messages in WeChat groups or on other social media are not tolerated.
2. Do not share accounts, do not submit another team's work, and do not create multiple teams to multiply the daily limit.
3. Report platform defects to the organizers instead of exploiting them. Reports of scorer or sandbox issues are welcome and credited.
4. Personal API tokens for the command-line tool act as the account that created them, with the same permissions, daily limits and quotas as the website. Actions taken with a token are attributed to that account. Do not give a token to anyone outside your team; revoke it on the profile page when it is no longer needed.
5. Decisions of the organizers on eligibility, disqualification, and awards are final.

## 9. Data and privacy

1. Registration data (name, email, affiliation, GitHub handle) is used only to run the event and to contact winners.
2. Private ZIP projects, results, run records and score reports are visible only to the submitting team and organizers and are kept by the organizers and may be used for academic research and publications; published material is anonymized unless the team agrees otherwise. Forks of public repositories remain public; use ZIP for private projects. Model keys never enter project repositories or frontend code.
3. Team names, scores, and ranks are public.
4. Challenge data, task cards, evaluation code and example projects provided by the organizers are licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) (attribution, non-commercial); please cite the GOSIM 2026 Agentic Observer Hackathon ([https://create.gosim.org/survey26/](https://create.gosim.org/survey26/)) when using them. Your own agent code is not restricted by this. The organizers will publish an article on this benchmark; once out, please cite it.

Contact: hackathon@gosim.org
