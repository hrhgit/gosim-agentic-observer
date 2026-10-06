import json, statistics, time, urllib.request
URL = "http://127.0.0.1:8019/v1/systemone"
def call(state, questions):
    req = urllib.request.Request(URL, data=json.dumps({"model": "kev-latest", "state": state, "questions": questions}).encode(),
                                 headers={"content-type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    return (time.perf_counter() - t) * 1000, body
FAULT = {"type": "noul", "instructions": "Is the telescope instrument itself faulty right now (a hidden instrument fault), rather than the weather or a site closure being the cause?"}
short = ("Telescope status. Throughput fell to about half of the level one hour ago. The change happened between two consecutive exposures. "
         "The sky brightness band did not change. No weather bulletin is active. No earthquake in the last ten nights.")
series = "Recent exposures (time, throughput relative to nominal, band check):\n" + "\n".join(
    f"{h:02d}:{m:02d} throughput {v:.2f} band {'match' if b else 'miss'}" for h, m, v, b in
    [(1,5,0.93,1),(1,22,0.91,1),(1,40,0.94,1),(1,58,0.92,1),(2,15,0.90,1),(2,33,0.47,1),(2,50,0.45,1),(3,8,0.46,1)]) + "\nBulletins: none. Earthquake recently: no."
MODE = {"type": "choice", "instructions": "Which observing mode fits the situation best for the next hour?",
        "criteria": {"rescue_setting_sky": "A part of the sky that will soon be unobservable is behind schedule",
                     "go_deep": "Conditions are excellent; spend time on faint targets",
                     "sweep_bright": "Conditions are poor; collect bright targets quickly",
                     "serve_request": "A time-limited request is close to its deadline",
                     "hold": "The site is probably closed; wait"}}
SEV = {"type": "score", "instructions": "How bad are the observing conditions?", "criteria": ["good", "mediocre", "poor"]}
cases = {"1 noul, short state": (short, {"fault": FAULT}),
         "1 noul, numeric series": (series, {"fault": FAULT}),
         "1 choice(5), short state": (short, {"mode": MODE}),
         "noul + choice(5) + score(3)": (short, {"fault": FAULT, "mode": MODE, "sev": SEV})}
for _ in range(3): call(short, {"fault": FAULT})
for name, (state, qs) in cases.items():
    ms, srv, tok = [], [], 0
    for _ in range(40):
        t, body = call(state, qs); ms.append(t); srv.append(body.get("latency_ms", 0)); tok = body["usage"]["input_tokens"]
    q = statistics.quantiles(ms, n=10)
    print(f"{name:30s} tokens {tok:4d}  client ms: median {statistics.median(ms):6.0f}  p90 {q[8]:6.0f}  max {max(ms):6.0f}   server-reported median {statistics.median(srv):6.0f}")
    last = body
print(json.dumps(last["answers"], indent=1)[:900])
