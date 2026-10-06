export default {
  nav: {
    vision: 'Event Brief',
    about: 'Why',
    mission: 'Mission',
    participate: 'Participate',
    leaderboard: 'Leaderboard',
    applyNow: 'Register now',
  },
  vision: {
    pageTitle: 'Agent Observer',
    pageSubtitle: 'A GOSIM Hackathon for Intelligent Survey Operations',
    intro:
      'This participant briefing explains why survey operations need agent observers, what a human leading observer does, how the challenge works across three planning horizons, what you submit, how scoring works, the astronomy you need, and the event schedule.',
    backToHome: 'Back to home',
    sections: [
      {
        kicker: '01 / Why',
        title: 'The observing plan shapes the science sample',
        paragraphs: [
          'A survey telescope maps large areas of sky systematically. It does not spend a whole night on a single object; it visits thousands of tiles across many nights, building up statistically powerful samples of galaxies, quasars, and stars.',
          'Cosmology runs on samples like these: large, uniform, well calibrated. Wide surveys reveal the large-scale structure of the universe, constrain its expansion history, underpin dark energy measurements, and tie galaxy evolution to cosmic time. Spectroscopic surveys add a third dimension — redshift. Position on the sky plus redshift gives you a three-dimensional map of cosmic structure. DESI is one concrete, public example: spectra for large samples of galaxies and quasars, used to map the expanding universe and study dark energy.',
          'But survey operations are hard. Weather changes what a 900-second exposure is worth through seeing, transparency, and sky brightness. Target visibility shifts with airmass and position on the sky. Observing programs prefer different conditions: DARK, BRIGHT, and BACKUP. The footprint has to be covered broadly and evenly for the science to hold up. High-priority tiles, nearly finished tiles, and under-covered regions all compete for the same block of time. One bad tactical choice spends real observing time and changes every plan that follows.',
          'Modern AI systems open a path: an assistant that reads state, calls computational tools, reasons under physical constraints, proposes a plan, monitors quality, and explains every choice. The long-term goal is an observing agent that can genuinely take part in real survey operations. This hackathon narrows that long-term vision into one focused benchmark.',
        ],
        highlight:
          'You build the observer’s logic. We provide the mission definition, tile table, observing constraints, weather scenarios, simulator, and evaluation protocol.',
        figure: {
          key: 'redshiftMap',
          alt: 'A survey map built from sky coordinates and redshift',
          caption: 'Sky position plus redshift becomes a map — which is what a survey ultimately delivers.',
          credit: 'David Kirkby / DESI collaboration · CC BY 4.0',
        },
      },
      {
        kicker: '02 / The Observer',
        title: 'What a human lead observer actually does',
        paragraphs: [
          'A seasoned lead observer makes a full set of decisions every 900-second slot: understand the science goal and decide tonight’s tactical priority; read seeing, transparency, sky brightness, and the short-term forecast; judge whether tonight suits DARK, BRIGHT, or BACKUP observing; keep the tiles that are visible now, legal, and worth the time; trade off science yield, priority, airmass, footprint balance, and wasted time; finish a tile when completing it beats over-exposure; replan around weather systems, forest-fire smoke, scheduled rocket launches, and changing efficiency; use fast validation signals to calibrate later decisions; and explain why this action was chosen in this slot.',
          'These duties are what the observing agent is taking over. We do not expect an agent to outperform a human immediately. We expect it to be evaluated objectively against a fixed interface, fixed data, and fixed scoring — so it can become a viable component of real survey operations.',
        ],
        duties: [
          { n: '01', title: 'Night strategy', desc: 'Understand the science goal and decide tonight’s tactical priority.' },
          { n: '02', title: 'Reading the weather', desc: 'Seeing, transparency, sky brightness, and the short-term forecast.' },
          { n: '03', title: 'Program selection', desc: 'Judge whether tonight suits DARK, BRIGHT, or BACKUP observing.' },
          { n: '04', title: 'Candidate filtering', desc: 'Keep the tiles that are visible now, legal, and worth the time.' },
          { n: '05', title: 'Tile ranking', desc: 'Trade off science yield, priority, airmass, footprint balance, and wasted time.' },
          { n: '06', title: 'Completion management', desc: 'Finish a tile when the value of completing it beats the cost of over-exposure.' },
          { n: '07', title: 'Replanning', desc: 'React to forecast disruptions, inserted requests, and changing observing efficiency.' },
          { n: '08', title: 'Quality monitoring', desc: 'Use fast validation signals to calibrate later decisions.' },
          { n: '09', title: 'Logging the reason', desc: 'Explain why the observer chose this action in this slot.' },
        ],
      },
      {
        kicker: '03 / Challenge Format',
        title: 'The Survey Mission Card and the challenge loop',
        paragraphs: [
          'Every challenge instance starts from a Survey Mission Card. It states the science goal, footprint area, time budget, telescope site, available observing programs, target classes, observing constraints, and scoring rules. The mission card and public development data — tile catalogue, example weather scenarios, initial state, and visual reports — will be released before the hackathon begins.',
          'Every participant faces the same evaluation scenario: the overall survey plan, per-night seeing and weather replay, ad-hoc observing requests, predictable mid-term disruptions, simulator, and scoring rule are fixed. The agent receives the current state and returns the next observing action every 900 seconds.',
        ],
        loopTitle: 'The challenge loop',
        loop: [
          { stage: 'Fixed inputs', items: ['Science goal', 'Footprint tiles', 'Weather replay'] },
          { stage: 'Agent state', items: ['Weather + forecast', 'Survey progress', 'Available tiles'] },
          { stage: 'Reasoning', items: ['Rank', 'Explain', 'Decide'] },
          { stage: 'Action', items: ['Observe', 'or wait'] },
          { stage: 'Metrics', items: ['Science yield', 'Uniformity + waste', 'Violations'] },
        ],
      },
      {
        kicker: '04 / Planning Horizons',
        title: 'Plan across the survey, the week, and the next 900 seconds',
        paragraphs: [
          'Long term — the organizers provide the overall survey plan in the mission card, while the agent manages that plan in flight. Final scoring includes survey completeness and completion quality.',
          'Middle term — the agent builds a flexible month, week, and day plan from progress and forecast conditions, absorbing weather systems, forest-fire smoke, scheduled launches, and newly inserted observing requests. This layer may be enabled as mission cards evolve.',
          'Short term — every 900-second slot, the agent makes an immediate tactical choice using the middle-term plan and real-time seeing and transparency. This is the focus of the scored benchmark.',
        ],
      },
      {
        kicker: '05 / Participant Job',
        title: 'Build a reproducibly runnable observer project',
        paragraphs: [
          'Implement the current platform agent interface. Online competition packages run in Python 3.12; download the platform starter kit. Examples here explain observing strategies; follow the platform documentation for the official interface.',
          'Practice accepts results files or agent packages. The online competition accepts only agent packages, run by the platform against hidden scenarios.',
          'Read the platform rules and docs, download the starter kit, develop on public scenarios, then submit through the platform. Scoring reports, agent logs and decision replays are available there.',
        ],
        actionCode: `return {
    "action": "observe",
    "tile_id": 100123,
    "plan": "DARK",
    "reason": "good weather and highest useful gain",
}`,
      },
      {
        kicker: '06 / Scoring',
        title: 'Scientific, reproducible, inspectable evaluation',
        paragraphs: [
          'Base science, program bonus and request reward, minus penalties. Multi-scenario phases use the mean score; standings use each team’s best scored submission. The platform’s current scoring version, challenge-score-v3, is authoritative.',
          'Evaluation follows open-science principles: fixed data and fixed scoring let every submitted observation result be evaluated consistently; readable result artifacts make outcomes inspectable; separate development and final-evaluation weather compare generalization instead of tuning to a known scenario.',
          'Visualization turns completed tile fractions into an observed-target map — a 3D spatial sample and a 2D butterfly plot of sky position plus simulated redshift, in the style DESI uses — so different strategies produce visibly different maps.',
          'The same observer interface can support future mission cards for real survey-log replay, z > 5 quasar planning, LBG planning, and target-of-opportunity inserts; the community can propose new fixed datasets for follow-up tracks.',
        ],
        formula: `score = base_science + program_bonus + request_reward
  - penalty_total`,
        figure: {
          key: 'reviewOverlay',
          alt: 'Review overlay of the fixed target catalog and an observed sample',
          caption:
            'The review overlay: grey is the full target catalog, colour is what one agent actually observed. The policy difference is visible at a glance.',
          plate: true,
        },
      },
      {
        kicker: '07 / Glossary',
        title: 'A little astronomy is enough',
        paragraphs: [
          'You do not need an astronomy background to read the state dictionary and reason about it. A Tile is a patch of sky observed in one pointing; the Footprint is the full survey area; Targets are the object counts inside each tile. High-priority tiles carry extra scientific weight, nearly complete tiles are one exposure from completion, and under-covered regions are lagging behind the rest of the footprint.',
          'Seeing describes atmospheric blur — lower is better. Transparency describes how clear the atmosphere is — higher is better. Sky brightness is background glow; darker is better for faint targets. Airmass measures how much atmosphere the light crosses and changes with sky position — lower is better. DARK, BRIGHT, and BACKUP are programs matched to dark, bright, and poor conditions. donefrac is tile completion: 0 means just started, 1 means finished.',
          'The state dictionary contains weather (current seeing, transparency, sky brightness, program, observability, and speed), forecast (this slot plus the next four), progress (tiles completed, mean completion, footprint balance, wasted time, and violations), and available_tiles (the legal options right now, with expected gain, expected waste, target counts, priority, and airmass).',
        ],
        actionCode: `return {
    "action": "observe",
    "tile_id": 100123,
    "plan": "DARK",
    "reason": "good weather and highest useful gain",
}`,
      },
      {
        kicker: '08 / Timeline',
        title: 'Three stages from training to awards',
        paragraphs: [
          'The event has three stages: online training from October 1–4, online competition from October 5–7, and Awards Day at GOSIM Shenzhen on October 17.',
          'Training introduces CosmosBench, the shared simulator, mission cards, and the observer interface. During the online competition, registered teams submit agent packages; the Agent Observer platform runs and scores them and publishes standings.',
        ],
        rounds: [
          { number: '01', name: 'Online Training', dates: 'Oct 1–4', format: 'Online', challenge: 'Learn CosmosBench, the shared simulator, mission cards, and the observer interface, then prepare with public development scenarios.', participants: 'Open worldwide to individuals and teams.' },
          { number: '02', name: 'Online Competition', dates: 'Oct 5–7', format: 'Online', challenge: 'Submit an agent package for the Agent Observer platform to run, evaluate and rank.', participants: 'All registered teams.' },
          { number: '03', name: 'Awards Day', dates: 'Oct 17', format: 'GOSIM Shenzhen', challenge: 'Final results are announced and prizes are presented.', participants: 'Winning teams and invited participants.' },
        ],
      },
    ],
  },
  hero: {
    eyebrow: 'GOSIM Hackathon · Intelligent Survey Operations',
    system: 'Agent Observer',
    subtitle: '巡天智能体',
    lede:
      'Build a future agent observer for survey nights: read the sky state, reason like a leading observer, and choose the next observation every 900 seconds.',
    pipeline: [
      { label: 'Online Training', date: 'Oct 1–4' },
      { label: 'Online Competition', date: 'Oct 5–7' },
      { label: 'Awards Day', date: 'Oct 17' },
    ],
    location: ['Online training Oct 1–4 · Online competition Oct 5–7', 'October 17 · Awards at GOSIM Shenzhen'],
    cta: 'Mission card coming soon',
  },
  home: {
    vision: {
      kicker: '01 / Vision',
      title: 'Build an open benchmark for intelligent survey operations',
      paragraphs: [
        'Every night a survey telescope faces thousands of observable tiles. Weather changes, target visibility changes, and science priorities change. Every decision a human observer makes shapes the quality of the final cosmic sample.',
        'We are building an open benchmark: an agent reads the state of the night sky, reasons like a seasoned observer, and decides the next observation every 900 seconds. Every participant receives the same survey plan, weather replay, inserted requests, simulator, and scoring rule — the only variable is strategy.',
      ],
    },
    mission: {
      kicker: '02 / Mission',
      title: 'Where should the telescope look next',
      lede: 'Your agent takes over the core decisions of a human lead observer.',
      cards: [
        { title: 'Read the state', desc: 'Weather, forecast, survey progress, and the tiles that are legal right now, packed into one compact state dictionary.' },
        { title: 'Weigh the trade-offs', desc: 'Spend the good dark time on faint targets, or finish the tile that is one exposure from done? The forecast improves later — is another slot worth the wait?' },
        { title: 'Make the call', desc: 'Observe a tile, or wait. One action every 900 seconds, and the reason for it.' },
      ],
      cardKicker: 'How the challenge arrives',
      cardTitle: 'One Survey Mission Card',
      cardLede: 'Every round of the challenge starts from a mission card. Everything that round holds you to is written on it:',
      cardItems: [
        { term: 'Science goal', desc: 'What this survey is actually trying to answer.' },
        { term: 'Footprint', desc: 'How much sky to cover, and how it divides into tiles.' },
        { term: 'Time budget', desc: 'How many 900-second slots there are to spend.' },
        { term: 'Telescope site', desc: 'Latitude, visibility limits, slot length, and the airmass constraint.' },
        { term: 'Available programs', desc: 'Which of DARK, BRIGHT and BACKUP this round opens up.' },
        { term: 'Target classes', desc: 'Which objects count toward yield, and what each is worth.' },
        { term: 'Observing constraints', desc: 'What you may not do, and what crossing the line costs.' },
        { term: 'Scoring rule', desc: 'How the score is computed and which penalties apply.' },
      ],
      cardNote: 'The authoritative input and output formats and the interface contract are defined by the mission card. The card and the public development data — tile catalogue, example weather scenarios, initial state and visual reports — are released before the hackathon begins.',
    },
    participate: {
      kicker: '04 / How to participate',
      title: 'Three steps to compete',
      lede: 'From training to awards, the path is clear.',
      steps: [
        { n: '01', title: 'Register & team up', desc: 'Sign up online and create or join a team. Solo developers are welcome too.' },
        { n: '02', title: 'Build your agent', desc: 'Download the platform starter kit, implement the current agent interface, and test on public practice scenarios.' },
        { n: '03', title: 'Submit for evaluation', desc: 'Practice accepts results files or agent packages. The online competition requires an agent package, which the platform runs and evaluates.' },
      ],
      timeline: [
        { label: 'Oct 1–4', desc: 'Online training' },
        { label: 'Oct 5–7', desc: 'Online competition' },
        { label: 'Oct 17', desc: 'Awards at GOSIM Shenzhen' },
      ],
      cta: 'Register now',
    },
    prizes: {
      kicker: '05 / Awards',
      title: '$5,500 in prizes',
      lede: 'Three award tiers recognize the strongest observing agents.',
      tiers: [
        { place: 'First Prize', amount: '$2,000', count: '1 winner' },
        { place: 'Second Prize', amount: '$1,000', count: '2 winners' },
        { place: 'Third Prize', amount: '$500', count: '3 winners' },
      ],
    },
    leaderboard: {
      kicker: '06 / Leaderboard',
      title: 'Live standings',
      lede: 'The Agent Observer platform provides the online competition standings. Visit the full board for practice standings and phase status.',
    },
  },
  about: {
    kicker: '01 / Why',
    title: 'The observing plan is what shapes the final science sample',
    p1: 'A survey telescope maps large areas of sky systematically. It does not spend a whole night on a single object; it visits thousands of tiles across many nights, building up statistically powerful samples of galaxies, quasars, and stars.',
    p2: 'Cosmology runs on samples like these: large, uniform, well calibrated. Wide surveys reveal the large-scale structure of the universe, constrain its expansion history, underpin dark energy measurements, and tie galaxy evolution to cosmic time. Spectroscopic surveys add a third dimension — redshift. Position on the sky plus redshift gives you a three-dimensional map of cosmic structure.',
    p3: 'DESI is one concrete, public example of exactly this: spectra for large samples of galaxies and quasars, used to map the expanding universe and study dark energy.',
    hardTitle: 'Why survey operations are hard',
    hardItems: [
      { term: 'Weather', desc: 'Changes what a 900-second exposure is worth, through seeing, transparency, and sky brightness.' },
      { term: 'Target visibility', desc: 'Shifts through the night with airmass and position on the sky.' },
      { term: 'Observing programs', desc: 'Each prefers different conditions: DARK, BRIGHT, and BACKUP.' },
      { term: 'The footprint', desc: 'Has to be covered broadly and evenly for the science analysis to hold up.' },
    ],
    hardClosing:
      'High-priority tiles, nearly finished tiles, and under-covered regions all compete for the same block of time. One bad tactical choice spends real observing time and changes every plan that follows. Night operations demand decisions that are both effective and explainable.',
    whyNowTitle: 'Why an observing agent, and why now',
    whyNow:
      'The long-term goal is an observing agent that can genuinely take part in real survey operations. A human lead observer translates science goals, weather, instrument state, tile completion, and candidate fields into a tactical decision, right now. Modern AI systems open a path: an assistant that reads state, calls computational tools, reasons under physical constraints, proposes a plan, monitors quality, and explains every choice.',
    whyNowClosing: 'This hackathon narrows that long-term vision into one focused benchmark.',
    split: [
      { side: 'You build', desc: 'The observer’s logic — the system that decides.' },
      { side: 'We provide', desc: 'The mission definition, tile table, observing constraints, weather scenarios, simulator, and evaluation protocol.' },
    ],
  },
  observer: {
    kicker: '02 / The Observer',
    title: 'What a human lead observer actually does',
    intro: 'These nine jobs are what the observing agent is taking over.',
    duties: [
      { n: '01', title: 'Night strategy', desc: 'Understand the science goal and decide tonight’s tactical priority.' },
      { n: '02', title: 'Reading the weather', desc: 'Seeing, transparency, sky brightness, and the short-term forecast.' },
      { n: '03', title: 'Program selection', desc: 'Judge whether tonight suits DARK, BRIGHT, or BACKUP observing.' },
      { n: '04', title: 'Candidate filtering', desc: 'Keep the tiles that are visible now, legal, and worth the time.' },
      { n: '05', title: 'Tile ranking', desc: 'Trade off science yield, priority, airmass, footprint balance, and wasted time.' },
      { n: '06', title: 'Completion management', desc: 'Finish a tile when the value of completing it beats the cost of over-exposure.' },
      { n: '07', title: 'Replanning', desc: 'React to forecast disruptions, inserted requests, and changing observing efficiency.' },
      { n: '08', title: 'Quality monitoring', desc: 'Use fast validation signals to calibrate later decisions.' },
      { n: '09', title: 'Logging the reason', desc: 'Explain why the observer chose this action in this slot.' },
    ],
  },
  challenge: {
    kicker: '03 / Format',
    title: 'The Survey Mission Card',
    intro:
      'Every challenge instance starts from a Survey Mission Card. It defines the science goal, footprint area, time budget, telescope site, observing programs, target classes, constraints, scoring rules, and authoritative interface contract. The mission card and public development data will be released before the hackathon begins.',
    cards: [
      { title: 'Mission definition', desc: 'Which sky to cover, which targets matter, how much time you have.' },
      { title: 'Telescope / site', desc: 'Latitude, visibility limits, slot length, airmass constraints.' },
      { title: 'Public dev data', desc: 'Tile catalogue, example weather scenarios, initial state, and visual reports.' },
      { title: 'Final eval data', desc: 'Organizer-supplied weather replay, run in the same simulator.' },
      { title: 'Your deliverable', desc: 'Practice results files or agent packages; online competition accepts only agent packages.' },
      { title: 'One evaluation', desc: 'Every agent faces the same survey plan, weather replay, inserted requests, disruptions, simulator, and scoring rule.' },
    ],
    loopTitle: 'The challenge loop',
    loop: [
      { stage: 'Fixed inputs', items: ['Science goal', 'Footprint tiles', 'Weather replay'] },
      { stage: 'Agent state', items: ['Weather + forecast', 'Survey progress', 'Available tiles'] },
      { stage: 'Reasoning', items: ['Rank', 'Explain', 'Decide'] },
      { stage: 'Action', items: ['Observe', 'or wait'] },
      { stage: 'Metrics', items: ['Science yield', 'Uniformity + waste', 'Violations'] },
    ],
    loopNote: 'The only variable is agent strategy. The overall survey plan, weather replay, ad-hoc requests, predictable disruptions, simulator, and scoring rule are fixed for every participant.',
  },
  build: {
    kicker: '04 / Participant Job',
    title: 'What participants do',
    intro:
      'Follow the platform docs to build your agent. Practice accepts results files or agent packages; online competition requires packages, run by the platform in Python 3.12.',
    io: [
      { term: 'Input', desc: 'A small state dictionary: weather, forecast, survey progress, and the legal candidate tiles.' },
      { term: 'Output', desc: 'One observing plan for the next 900-second slot, such as observe a tile_id under DARK or wait. The reason text is free form.' },
      { term: 'Scoring', desc: 'Effective targets, tiles completed, evenness of footprint coverage, time efficiency, and action legality.' },
      { term: 'Deliverable', desc: 'Practice accepts results files or agent packages; online competition accepts only packages. Follow the platform format requirements.' },
    ],
    starterTitle: 'Illustrative example only',
    starterIntro:
      'This minimal utility rule illustrates a strategy. Follow the platform docs for the official package runtime and interface.',
    starterCode: `tiles = state["available_tiles"]
if len(tiles) == 0:
    return {"action": "wait"}

best = max(tiles, key=lambda t: t["expected_weighted_gain"])
return {"action": "observe", "tile_id": best["tile_id"], "plan": "DARK",
        "reason": "highest useful gain"}`,
    starterClosing:
      'Weather, completion, airmass, footprint balance, and waste are useful strategy factors. How to weigh them and how to structure the agent are left to each participant.',
    archTitle: 'Illustrative architectures',
    architectures: [
      { name: 'Utility agent', desc: 'Score every candidate tile, take the highest.' },
      { name: 'Weather-aware planner', desc: 'Use the forecast to decide whether to spend or save a high-value tile.' },
      { name: 'Completion manager', desc: 'Spot tiles near donefrac = 1 and manage over-exposure waste.' },
      { name: 'Footprint balancer', desc: 'Up-weight regions that are behind on coverage.' },
      { name: 'Risk-controlled agent', desc: 'Filter out high-waste, high-airmass, or low-value options before ranking.' },
      { name: 'Lead / support pair', desc: 'One module proposes the plan, another checks constraints and explains the risk.' },
      { name: 'LLM planner + rule checker', desc: 'Language reasoning for strategy, deterministic code for legality.' },
    ],
    archCode: `AgentObserver
  -> WeatherInterpreter
  -> ProgramSelector
  -> CandidateFilter
  -> TileRanker
  -> FootprintBalancer
  -> CompletionManager
  -> SupportObserver / RiskChecker
  -> ReasonLogger`,
    scenariosTitle: 'Situations you will meet',
    scenariosIntro: 'A strong agent handles a handful of recurring situations.',
    scenarios: [
      { when: 'Excellent dark night', then: 'Spend the slot on high expected gain, high priority, faint-target programs.' },
      { when: 'Marginal but usable weather', then: 'Prefer robust tiles, completion opportunities, and low-waste options.' },
      { when: 'Forecast improving', then: 'Compare current speed against future speed before spending a high-value tile.' },
      { when: 'Footprint unbalanced', then: 'Give under-covered regions a bonus.' },
      { when: 'Tile near completion', then: 'Finish it when the completion bonus beats the waste cost.' },
      { when: 'High airmass', then: 'Discount the utility of tiles that are harder to observe well.' },
    ],
  },
  scoring: {
    kicker: '03 / Evaluation',
    title: 'Where the score comes from, and why it holds up',
    intro:
      'Base science, program bonus and request reward, minus penalties. Multi-scenario phases use the mean score; standings use each team’s best scored submission.',
    formula: `score = base_science + program_bonus + request_reward
  - penalty_total`,
    formulaVersion: 'platform scoring version challenge-score-v3',
    note:
      'Any internal decision logic is fair game. But a good agent always ends up trading immediate yield against completion, uniformity, weather risk and operational discipline — which is exactly what these terms are there to force.',

    stressTitle: 'Where the field separates: after the weather turns',
    stress:
      'The weather replay carries deterministic disruption blocks — rain, forest-fire smoke, a scheduled rocket launch. Everyone meets the same ones. The difference is what you do about them.',
    stressWeak: 'The naive way',
    stressWeakDesc: 'Sit and wait the time away, or spend your best dark-time targets on sky that cannot carry them.',
    stressStrong: 'The strong way',
    stressStrongDesc: 'Move on the forecast early, switch programs when the night calls for it, finish tiles that are nearly done, and hold the valuable dark-time targets until conditions come back.',

    principlesTitle: 'Why the result can be trusted',
    principles: [
      { term: 'Reproducible', desc: 'Fixed seeds, fixed data, fixed scoring. Any submission can be re-run by a third party and land on the same number.' },
      { term: 'Auditable turn by turn', desc: 'The state dictionary, the run history and the full per-turn trace logs are all readable, so every decision can be checked after the fact.' },
      { term: 'Hard to overfit', desc: 'Development weather and final evaluation weather are kept apart, so the score compares generalization, not tuning to a known scenario.' },
      { term: 'One variable only', desc: 'You control the agent policy and its reasoning. The simulator, data, weather replay, constraints and scoring are ours, and identical for everyone.' },
      { term: 'Clearly bounded', desc: 'Short-term observation planning is the primary evaluation; middle-term planning is enabled at organizer discretion as mission cards evolve.' },
    ],

    vizTitle: 'What a finished run looks like',
    vizNote: 'The map is the benchmark’s visualization layer. What actually gets scored is still the planning.',
  },
  glossary: {
    kicker: '06 / Glossary',
    title: 'A little astronomy is enough',
    intro: 'You do not need an astronomy background to read the state dictionary and reason about it. This much will do.',
    terms: [
      { term: 'Tile', desc: 'A small patch of sky the telescope can observe in one pointing.' },
      { term: 'Footprint', desc: 'The whole sky area the survey intends to cover.' },
      { term: 'Targets', desc: 'In this planning benchmark, the object counts inside each tile.' },
      { term: 'Seeing', desc: 'How blurred the image is. Lower is better.' },
      { term: 'Transparency', desc: 'How clear the atmosphere is. Higher is better.' },
      { term: 'Sky brightness', desc: 'Background noise. For faint targets, darker is usually better.' },
      { term: 'Airmass', desc: 'How much atmosphere the light passes through. Lower is better.' },
      { term: 'DARK / BRIGHT / BACKUP', desc: 'Observing programs matched to dark, bright, and poor sky conditions.' },
      { term: 'Priority and coverage', desc: 'High-priority, nearly complete, and under-covered tiles compete for each observing slot.' },
      { term: 'donefrac', desc: 'Tile completion: 0 means just started, 1 means finished.' },
    ],
    stateTitle: 'What is in the state',
    state: [
      { field: 'weather', desc: 'Current seeing, transparency, sky brightness, program, observability, and speed.' },
      { field: 'forecast', desc: 'This slot plus the next four, which is what makes replanning possible.' },
      { field: 'progress', desc: 'Tiles completed, mean completion, footprint balance, wasted time, and violations.' },
      { field: 'available_tiles', desc: 'The legal options right now, with expected gain, expected waste, target counts, priority, and airmass.' },
    ],
    actionCode: `return {
    "action": "observe",
    "tile_id": 100123,
    "plan": "DARK",
    "reason": "good weather and highest useful gain",
}`,
  },
  schedule: {
    kicker: '07 / Timeline', title: 'Schedule',
    intro: 'Online training runs October 1–4, the CosmosBench-powered online competition runs October 5–7, and Awards Day is October 17.',
    challengeLabel: 'Survey task', participantsLabel: 'Who participates',
    rounds: [
      { number: '01', name: 'Online Training', dates: 'Oct 1–4', format: 'Online', challenge: 'Learn CosmosBench, the shared simulator, mission cards, and the observer interface, then prepare with public development scenarios.', participants: 'Open worldwide to individuals and teams.' },
      { number: '02', name: 'Online Competition', dates: 'Oct 5–7', format: 'Online', challenge: 'Submit an agent package for the Agent Observer platform to run, evaluate and rank.', participants: 'All registered teams.' },
      { number: '03', name: 'Awards Day', dates: 'Oct 17', format: 'GOSIM Shenzhen', challenge: 'Final results are announced and prizes are presented.', participants: 'Winning teams and invited participants.' },
    ],
  },
  leaderboard: {
    kicker: '08 / CosmosBench', title: 'Live Leaderboard · Top 20',
    intro: 'One mission card, weather replay, simulator, and scoring function. Evaluation and live standings are powered by CosmosBench.',
    refresh: 'Refresh', full: 'Full board', updated: 'Updated', loading: 'Loading competition platform…', empty: 'No public online competition scores yet. Visit the platform for phase status and practice standings.', unavailable: 'The leaderboard could not be loaded. Open the full board on the competition platform.',
    team: 'Team / Agent', score: 'Score', science: 'Science', completion: 'Completion', uniformity: 'Uniformity', submissions: 'Scored submissions',
  },
  cta: {
    title: 'Hand the next move of the night sky to the agent you wrote',
    tagline: 'Same survey plan, weather replay, inserted requests, simulator, and scoring rule. The only variable is strategy.',
    location: 'Online development and CosmosBench evaluation · October 17 at GOSIM Shenzhen',
  },
  footer: {
    copyright: '2026 GOSIM · Agent Observer',
    mainSite: 'GOSIM',
    register: 'Mission card coming soon',
  },
  construction: {
    title: 'Site Under Construction',
    desc: 'Registration, teams, development documentation and submissions are available on the Agent Observer platform. Check platform announcements for updates.',
    ok: 'Got it',
  },
}
