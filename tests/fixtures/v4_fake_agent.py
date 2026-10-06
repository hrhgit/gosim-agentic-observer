"""A self-contained participant-agent-protocol-v4 test agent (standard library only).

Modes (argv[1]):
  greedy       night-aware: wait_until the next night by day; at night observe the
               highest not-yet-observed target on fibre 5 for 900 s (default)
  wait         answer every request with a 3600 s wait
  bad-after:N  greedy for N decisions, then an invalid action (fibre 16)
  garbage-after:N  greedy for N decisions, then a non-JSON line on stdout
  sleep        never answers (wall-clock expiry)
  sleep-after:N    greedy for N decisions, then never answers (expiry with a history)
  finish-after:N   greedy for N decisions, then {"action": "finish"}
  spin         computes forever instead of answering (fair-clock expiry on CPU time)
  background   greedy, but a thread keeps computing between requests (charged as well)
  nap          greedy after waiting 1 s per request (like a model call)
Every mode logs "INIT <json>" (a summary of initialize) and "FINISH-MSG <json>" to stderr.
With PROBE=1 it also logs what it can see of the host ("PROBE <json>").
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

MODE = sys.argv[1] if len(sys.argv) > 1 else "greedy"


def log(tag, value):
    sys.stderr.write(tag + " " + json.dumps(value, sort_keys=True) + "\n")
    sys.stderr.flush()


def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def lst_deg(moment, lon):
    days = moment.timestamp() / 86400.0 + 2440587.5 - 2451545.0
    return (280.46061837 + 360.98564736629 * days + lon) % 360.0


def altaz(ra, dec, moment, lat, lon):
    ha = math.radians((lst_deg(moment, lon) - ra + 180.0) % 360.0 - 180.0)
    la, de = math.radians(lat), math.radians(dec)
    sin_alt = math.sin(la) * math.sin(de) + math.cos(la) * math.cos(de) * math.cos(ha)
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))
    cos_alt = max(1e-12, math.cos(alt))
    sin_az = -math.sin(ha) * math.cos(de) / cos_alt
    cos_az = (math.sin(de) - math.sin(alt) * math.sin(la)) / (cos_alt * max(1e-12, math.cos(la)))
    return math.degrees(alt), math.degrees(math.atan2(sin_az, cos_az)) % 360.0


state = {"init": None, "observed": set(), "count": 0}


def greedy(payload):
    init = state["init"]
    now = parse(payload["now_utc"])
    nights = init["survey"]["nights"]
    current = next((n for n in nights if parse(n["observing_start_utc"]) <= now < parse(n["observing_end_utc"])), None)
    if current is None:
        upcoming = next((n for n in nights if parse(n["observing_start_utc"]) > now), None)
        if upcoming is None:
            return {"action": "finish"}
        return {"action": "wait", "until_utc": upcoming["observing_start_utc"]}
    site = init["site"]
    columns = init["targets"]["columns"]
    index = {name: columns.index(name) for name in columns}
    best = None
    for row in init["targets"]["rows"]:
        target_id = row[index["target_id"]]
        if target_id in state["observed"]:
            continue
        alt, az = altaz(row[index["ra_deg"]], row[index["dec_deg"]], now, site["latitude_deg"], site["longitude_deg"])
        if 45.0 < alt < 80.0 and (best is None or alt > best[0]):
            best = (alt, az, target_id)
    if best is None:
        return {"action": "wait", "duration_seconds": 900}
    alt, az, target_id = best
    state["observed"].add(target_id)
    pitch = init["instrument"]["pitch_deg"]
    d_alt = d_az = -0.5 * pitch  # fibre 5 = row 1, column 1 of the 4 x 4 grid
    cmd_alt = alt - d_alt
    cmd_az = (az - d_az / math.cos(math.radians(cmd_alt))) % 360.0
    return {"action": "observe", "pointing": {"alt_deg": cmd_alt, "az_deg": cmd_az},
            "assignments": {"5": target_id}, "duration_seconds": 900, "program": "BACKUP"}


def answer(message):
    payload = message["payload"]
    state["count"] += 1
    if MODE == "wait":
        return {"action": "wait", "duration_seconds": 3600}
    if MODE == "nap":
        time.sleep(1)
    if MODE == "spin":
        while True:
            pass
    if MODE == "sleep" or MODE.startswith("sleep-after:") and state["count"] > int(MODE.split(":")[1]):
        time.sleep(3600)
    if MODE.startswith("bad-after:") and state["count"] > int(MODE.split(":")[1]):
        return {"action": "observe", "pointing": {"alt_deg": 60.0, "az_deg": 10.0},
                "assignments": {"16": "V4T000001"}, "duration_seconds": 900}
    if MODE.startswith("garbage-after:") and state["count"] > int(MODE.split(":")[1]):
        print("this is not json", flush=True)
        return None
    if MODE.startswith("finish-after:") and state["count"] > int(MODE.split(":")[1]):
        return {"action": "finish"}
    return greedy(payload)


if os.environ.get("PROBE") == "1":
    log("PROBE", {"bundle_visible": os.path.exists(os.environ.get("PROBE_PATH", "/nonexistent")),
                  "admin_key_visible": "SUPABASE_SERVICE_ROLE_KEY" in os.environ,
                  "docker_socket_visible": os.path.exists("/var/run/docker.sock")})

if MODE == "background":
    import threading

    def _spin():
        while True:
            pass
    threading.Thread(target=_spin, daemon=True).start()

for line in sys.stdin:
    message = json.loads(line)
    kind = message["message_type"]
    if kind == "initialize":
        state["init"] = message["payload"]
        payload = message["payload"]
        log("INIT", {"protocol_version": message["protocol_version"], "keys": sorted(payload),
                     "schema_version": payload["schema_version"], "targets": len(payload["targets"]["rows"]),
                     "nights": len(payload["survey"]["nights"]), "limits": payload["limits"],
                     "task_card": payload["task_card"]})
        continue
    if kind == "finish":
        log("FINISH-MSG", message)
        sys.exit(0)
    action = answer(message)
    if action is None:
        continue
    print(json.dumps({"protocol_version": message["protocol_version"], "message_type": "decision_response",
                      "decision_sequence": message["decision_sequence"], "reason": "test", **action}), flush=True)
