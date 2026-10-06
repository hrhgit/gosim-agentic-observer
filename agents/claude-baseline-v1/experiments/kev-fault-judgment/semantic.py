"""Can Kev map a bulletin event name it has never been told about onto a known way of handling it?"""
import json, time, urllib.request, statistics
URL = "http://127.0.0.1:8019/v1/systemone"
Q = {"type": "choice", "instructions": "A bulletin at an optical telescope announces this event. How should the observing software treat it while it lasts?",
     "criteria": {"site_closed": "Observing is impossible: precipitation, dangerous wind or the dome must stay shut",
                  "sky_degraded": "Observing continues, but the sky is dimmer, brighter or blurrier than normal",
                  "direction_blocked": "Only one part of the sky, in some direction, is unusable",
                  "instrument": "The telescope or instrument itself is affected, not the sky",
                  "no_effect": "No effect on observing"}}
ITEMS = [  # (event kind, expected, already known to the baseline?)
 ("rain", "site_closed", 1), ("storm", "site_closed", 1), ("overcast", "sky_degraded", 1), ("haze", "sky_degraded", 1),
 ("rocket_launch", "direction_blocked", 1), ("terrain_obstruction", "direction_blocked", 1), ("earthquake", "instrument", 1),
 ("snow", "site_closed", 0), ("hail", "site_closed", 0), ("high_wind", "site_closed", 0), ("sandstorm", "site_closed", 0), ("lightning", "site_closed", 0),
 ("thin_cirrus", "sky_degraded", 0), ("airborne_dust", "sky_degraded", 0), ("wildfire_smoke", "sky_degraded", 0), ("aurora", "sky_degraded", 0),
 ("bright_moonlight", "sky_degraded", 0), ("poor_seeing", "sky_degraded", 0),
 ("aircraft_corridor", "direction_blocked", 0), ("city_glow_to_the_east", "direction_blocked", 0), ("crane_at_neighbouring_dome", "direction_blocked", 0),
 ("laser_from_adjacent_telescope", "direction_blocked", 0),
 ("power_outage", "instrument", 0), ("cooling_failure", "instrument", 0), ("fibre_positioner_fault", "instrument", 0), ("detector_noise", "instrument", 0),
 ("staff_shift_change", "no_effect", 0), ("public_holiday", "no_effect", 0), ("visitor_tour", "no_effect", 0)]
ok = {0: 0, 1: 0}; tot = {0: 0, 1: 0}; lat = []; wrong = []
for kind, expect, known in ITEMS:
    req = urllib.request.Request(URL, data=json.dumps({"model": "kev-latest", "state": f'Bulletin notice: event_kind = "{kind}".', "questions": {"q": Q}}).encode(), headers={"content-type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r: a = json.load(r)["answers"]["q"]
    lat.append((time.perf_counter() - t) * 1000)
    tot[known] += 1; ok[known] += a["choice"] == expect
    if a["choice"] != expect: wrong.append((kind, expect, a["choice"], round(a["probabilities"][expect], 2)))
print(f"kinds the baseline already knows: {ok[1]}/{tot[1]} right; kinds it has never seen: {ok[0]}/{tot[0]} right; latency median {statistics.median(lat):.0f} ms")
for w in wrong: print("  wrong:", w)
