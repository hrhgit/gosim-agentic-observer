#!/usr/bin/env python3
"""Baseline survey agent for participant-agent-protocol-v4 (GOSIM Agentic Observer).

Deterministic, standard library only, no model calls: one JSON object per line on
stdin/stdout, logs on stderr. See README.md and docs/02-design.md.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback

from core.astro import DEG, format_utc, parse_utc
from core.capture import Capture
from core.planner import Planner
from core.state import Survey

PROTOCOL = "participant-agent-protocol-v4"

# Tunable constants; BASE_<NAME> in the environment overrides one for an experiment.
KNOBS = {
    "kappa": 0.70,           # price of time = kappa * recent realised science rate
    "price_cap": 0.80,       # ... but never more than this fraction of the best rate available now
    "rate_hours": 6.0,       # memory of that rate, in hours of exposure time
    "request_gain": 3.0,     # weight of a request reward relative to its face value (it has a deadline)
    "request_floor": 2.5,    # least bonus of one still-needed request target, in units of the reward
    "urgency_power": 0.5,    # required-target urgency = (median visibility left / own) ** power
    "urgency_cap": 3.0,
    "sci_urgency_power": 0.5,  # same for ordinary science (0 = off)
    "sci_urgency_cap": 2.5,
    "partial_discount": 0.85,  # weight of a gain that leaves its target unsaturated (1 = no discount)
    "deep_weight": 0.25,       # weight of "only a longest exposure could add this" in the zone ranking
    "dark_probe_every": 8.0,   # declare DARK once per this many non-DARK exposures when recent nights were dark
    "timing_power": 3.0,     # required targets wait for (transmission now / best to come) ** power
    "k_max": 14.0,           # most candidate fields evaluated per decision
    "fine_shifts": 1.0,      # 1 = try a 5x5 instead of a 3x3 grid of sub-cell shifts
    "cpu_share": 0.85,       # fraction of the CPU budget planned for
    "wall_share": 0.80,      # fraction of the real-time cap planned for
    "calib_shots": 1.0,      # short opening exposures that check the pointing
    "final_probe": 1.0,      # spend a free report at the very end of the survey
    "log_every": 250.0,
}


def log(*parts) -> None:
    print(*parts, file=sys.stderr, flush=True)


def load_knobs() -> dict:
    knobs = dict(KNOBS)
    for name in knobs:
        raw = os.environ.get("BASE_" + name.upper())
        if raw:
            try:
                knobs[name] = float(raw)
            except ValueError:
                pass
    return knobs


class Agent:
    def __init__(self, payload: dict) -> None:
        self.knobs = load_knobs()
        started = time.process_time()
        self.sv = Survey(payload, self.knobs)
        self.planner = Planner(self.sv)
        sv = self.sv
        sv.sky.debug = bool(os.environ.get("BASE_DEBUG_SKY"))
        sv.sky.dark_probe_every = int(self.knobs["dark_probe_every"])
        self.pending = None
        self.night_seen = -1
        self.calib_left = int(self.knobs["calib_shots"])
        self.calib_extra = 0
        self.final_probe_done = False
        self.decisions = 0
        # remaining night seconds after each night, for pacing
        suffix = [0.0] * (len(sv.nights) + 1)
        for i in range(len(sv.nights) - 1, -1, -1):
            suffix[i] = suffix[i + 1] + (sv.nights[i][1] - sv.nights[i][0])
        self.suffix = suffix
        self.avg_t = 900.0
        self.k = 8.0
        self.own = 0.02          # CPU seconds per planning decision (EMA)
        self.engine = 0.02       # real seconds the engine takes between our reply and its next request
        self.engine0 = None      # (engine seconds, ledger load) early in the run, for the growth model
        self.entries = 0         # hit rows the engine has recorded so far
        self.requests_seen = 0
        self.now = 0.0
        self.reply_at = None
        self.t_floor = sv.t_min
        self.misses = 0
        self.assigned = 0
        self.predicted_gain = 0.0    # model check: science gain the planner expected vs what came back
        self.realised_gain = 0.0
        cat = sv.cat
        log(f"init: {cat.n} targets, {sum(cat.required)} required, {cat.nz} zones, {cat.nc} season cells, "
            f"{len(sv.nights)} nights, {sv.n_fibers} fibres pitch {sv.pitch:.4f} deg, "
            f"price0 {sv.price:.5f}/s, cpu {time.process_time() - started:.2f}s")

    # -- pacing ---------------------------------------------------------------------------
    def _engine_seconds(self, n_observes: float, nights_left: int, speed: float) -> float:
        """Projected real seconds the engine itself needs for the rest of the survey.

        Besides a per-exposure cost, the engine rescans its whole score ledger at every
        decision and once more per issued request, so its time per decision grows with
        (ledger entries) x (1 + requests issued): quadratic over a long survey. That time
        counts against the real-time cap of a card, so it has to be planned for."""
        sv = self.sv
        entries = float(self.entries)
        per_exposure = entries / sv.observes if sv.observes >= 10 else 0.8 * sv.n_fibers
        done = 1.0 - (max(0.0, sv.nights[min(self.night_seen, len(sv.nights) - 1)][1] - self.now)
                      + self.suffix[min(self.night_seen + 1, len(sv.nights))]) / sv.total_night_seconds
        requests_end = self.requests_seen / done if done > 0.15 else max(self.requests_seen, min(6.0, len(sv.nights) / 14.0))
        load_now = entries * (1.0 + self.requests_seen)
        if self.engine0 is None and sv.observes >= 40:
            self.engine0 = (self.engine, load_now)
        slope = 0.25e-6 * speed
        base = self.engine
        if self.engine0 is not None:
            base = self.engine0[0]
            if load_now - self.engine0[1] > 30000.0:
                slope = max(0.05e-6 * speed, (self.engine - self.engine0[0]) / (load_now - self.engine0[1]))
            base = max(0.002, base - slope * self.engine0[1])
        entries_mid = entries + per_exposure * n_observes / 2.0
        requests_mid = 0.5 * (self.requests_seen + requests_end)
        per_observe = base + slope * entries_mid * (1.0 + requests_mid)
        daytime_steps = nights_left * 12.0
        return n_observes * per_observe + daytime_steps * slope * entries_mid * requests_mid

    def _pace(self, payload: dict, t: float, night: int) -> None:
        """Set the search breadth (zones evaluated) and, if the real-time cap demands it, a
        minimum exposure length, from the CPU budget and the real time that are left."""
        sv = self.sv
        clock = payload.get("wallclock") or {}
        cpu_left = clock.get("remaining_real_cpu_seconds")
        if cpu_left is None:
            cpu_left = clock.get("remaining_seconds", 900.0)
        cpu_left = float(cpu_left)
        wall_left = clock.get("wall_remaining_seconds")
        speed = float(clock.get("speed_factor") or 1.0)
        self.now = t
        remaining = max(0.0, sv.nights[night][1] - t) + self.suffix[night + 1]
        nights_left = len(sv.nights) - night
        n_observes = remaining / max(self.avg_t, 60.0) * 1.05
        n_other = 2.0 * nights_left + 5.0
        per_decision = cpu_left * self.knobs["cpu_share"] / (n_observes + n_other)
        floor = sv.t_min
        if wall_left is not None:
            budget = float(wall_left) * self.knobs["wall_share"]
            minimum = 0.004 * speed
            spare = budget - self._engine_seconds(n_observes, nights_left, speed)
            per_wall = spare / (n_observes + n_other)
            if per_wall < per_decision:
                per_decision = per_wall
            if per_wall < minimum:
                # not even the engine fits at this cadence: stretch the exposures
                stretch = 1.0
                for _ in range(12):
                    stretch *= 1.2
                    fewer = n_observes / stretch
                    if self._engine_seconds(fewer, nights_left, speed) + (fewer + n_other) * minimum <= budget:
                        break
                floor = int(min(sv.t_max, self.avg_t * stretch))
                per_decision = minimum
            if float(wall_left) < 15.0 or cpu_left < 1.5:
                per_decision = 0.0
                floor = sv.t_max
        # Even the narrowest search costs something. If the CPU left does not cover that for
        # every remaining decision, make fewer decisions: stretch the exposures.
        cheapest = max(0.002, self.own * 2.0 / max(self.k, 2.0))
        if 0.0 < per_decision < cheapest:
            floor = max(floor, int(min(sv.t_max, self.avg_t * cheapest / per_decision)))
        ratio = per_decision / max(self.own, 1e-4)
        self.k = max(2.0, min(self.knobs["k_max"], self.k * max(0.6, min(1.4, math.sqrt(max(ratio, 0.0))))))
        self.t_floor = max(sv.t_min, floor)

    # -- feedback -------------------------------------------------------------------------
    def _digest(self, last: dict) -> None:
        rec = self.pending
        sv = self.sv
        cat = sv.cat
        hits = {}
        for hit in last.get("hits") or ():
            j = cat.index.get(hit.get("target_id"))
            if j is not None:
                hits[j] = float(hit.get("score", 0.0))
        rec["notices"] = sv.sky.notices_between(rec["t0"], rec["t0"] + rec["teff"], sv.slot)
        outcomes = []
        unexpected = 0
        for item in rec["items"]:
            hit = item[0] in hits
            outcomes.append((item[10], item[11], item[12], item[1], item[2], hit))
            if not hit:
                self.misses += 1
                if item[8]:
                    unexpected += 1
        self.assigned += len(rec["items"])
        self.entries += len(hits)
        was_active = sv.offset.active
        sv.offset.observe(rec["cmd_alt"], rec["cmd_az"], outcomes, unexpected)
        if sv.offset.active and (not was_active or unexpected):
            log(f"pointing: offset estimate alt {sv.offset.alt:+.4f} az {sv.offset.az:+.4f} deg "
                f"(+-{sv.offset.margin_alt:.4f}/{sv.offset.margin_az:.4f}), {unexpected} unexpected misses, "
                f"mismatch {sv.offset.best_mismatch}")
        factors = sv.sky.update(rec, hits, rec["night"])
        rows = [(j, score, factors[j][0], factors[j][1]) for j, score in hits.items() if j in factors]
        closed = bool(hits) and all(score <= 0.0 for score in hits.values())
        index = last.get("observe_index")
        gain = sv.apply_hits(int(index) if isinstance(index, int) else sv.observes, rows, rec["teff"], closed)
        if not closed:
            self.predicted_gain += rec["predicted"]
            self.realised_gain += gain
        self.avg_t += 0.05 * (rec["teff"] - self.avg_t)

    # -- one decision -----------------------------------------------------------------------
    def decide(self, payload: dict) -> dict:
        sv = self.sv
        sky = sv.sky
        t = parse_utc(payload["now_utc"])
        resyncs = []
        for message in payload.get("new_messages") or ():
            if not isinstance(message, dict):
                continue
            kind = message.get("record_type")
            if kind == "bulletin":
                try:
                    sky.bulletin(message, parse_utc(message["issued_at_utc"]))
                except (KeyError, TypeError, ValueError):
                    pass
            elif kind == "state_resync":
                resyncs.append(message)
            elif kind == "observation_request":
                self.requests_seen += 1
        last = payload.get("last_result")
        if isinstance(last, dict):
            if last.get("action") == "observe" and self.pending is not None:
                self._digest(last)
            elif last.get("action") == "report":
                correct = bool(last.get("correct"))
                sky.report_result(correct, t)
                log(f"report at {payload['now_utc']}: correct={correct} delta={last.get('score_delta')} "
                    f"free_left={sky.free_left}")
        self.pending = None
        for message in resyncs:
            dropped = sv.resync(message)
            log(f"state_resync: window {message.get('invalidated_window')} dropped {dropped} of our exposures")
        sv.set_requests(payload.get("active_requests"), t)

        night = sv.night_index(t)
        if night >= len(sv.nights):
            return {"action": "wait", "duration_seconds": sv.t_max}
        start, end = sv.nights[night]
        if t < start:
            return {"action": "wait", "until_utc": format_utc(start)}
        rem = end - t
        last_night = night == len(sv.nights) - 1
        if (last_night and rem <= 1500.0 and not self.final_probe_done and self.knobs["final_probe"] > 0
                and sky.free_left >= 1):
            # an unrepaired fault lasts to the end of the survey; a free report costs nothing
            self.final_probe_done = True
            return {"action": "report"}
        if rem < 45.0:
            target = sv.nights[night + 1][0] if not last_night else end
            return {"action": "wait", "until_utc": format_utc(target)}
        remaining_gain = 0.5 * sv.rate_gain / sv.rate_time * (rem + self.suffix[night + 1])
        if sky.want_report(t, remaining_gain):
            log(f"report: e={sky.e:.3f} W={sky.W:.3f} rho={sky.rho:.3f} low_count={sky.low_count} at {payload['now_utc']}")
            return {"action": "report"}
        boundary = (math.floor(t / sv.slot) + 1) * sv.slot
        if sky.site_closed_now:
            return {"action": "wait", "until_utc": format_utc(min(boundary, end))}
        if night != self.night_seen:
            self.night_seen = night
            sv.update_urgency(night)
            sv.update_band_bonus()

        self._pace(payload, t, night)
        ceiling = sv.t_max
        if sky.closed_suspect:
            # unannounced closures come in whole slots: do not commit past the next boundary
            ceiling = int(boundary - t)
            if ceiling < 240:
                ceiling += int(sv.slot)
        calibrate = self.calib_left > 0 or (sv.offset.uncertain and self.calib_extra < 8)
        floor = sv.t_min if (calibrate or sky.closed_suspect) else self.t_floor
        began = time.process_time()
        plan = self.planner.plan(t, night, int(self.k), floor, ceiling, calibrate)
        self.own += 0.1 * ((time.process_time() - began) - self.own)
        if plan is None:
            return {"action": "wait", "until_utc": format_utc(min(boundary, end))}
        if self.calib_left > 0:
            self.calib_left -= 1
        elif calibrate:
            self.calib_extra += 1
        return self._action(plan, t, night, rem)

    def _action(self, plan: dict, t: float, night: int, rem: float) -> dict:
        sv = self.sv
        site = sv.site
        cat = sv.cat
        sky = sv.sky
        duration = int(plan["duration"])
        if sky.suspect and duration > 2 * sv.t_min:
            # A suspected fault is confirmed or dismissed by measuring the throughput, and only
            # an unsaturated hit measures it. The plan saturates its targets at the low level;
            # stop a little short so the faintest ones stay readable.
            duration = max(sv.t_min, int(0.8 * duration))
        teff = min(float(duration), rem)
        centre = plan["centre"]
        _, zen, north, east = site.frame(t)
        sin_alt = centre[0] * zen[0] + centre[1] * zen[1] + centre[2] * zen[2]
        alt = math.asin(max(-1.0, min(1.0, sin_alt))) * DEG
        az = math.atan2(centre[0] * east[0] + centre[1] * east[1] + centre[2] * east[2],
                        centre[0] * north[0] + centre[1] * north[1] + centre[2] * north[2]) * DEG % 360.0
        cmd_alt = min(90.0, max(0.0, alt - sv.offset.alt))
        cmd_az = (az - sv.offset.az) % 360.0
        if cmd_az >= 360.0:
            cmd_az = 0.0
        samples = [t + teff * x for x in (1.0 / 6.0, 0.5, 5.0 / 6.0)]
        frames = [site.frame(s) for s in samples]
        moons = [site.moon(s) for s in samples]
        rho, w_level = sky.levels()
        directional = sky.directional
        n = sv.n_side
        ux, uy, uz = cat.ux, cat.uy, cat.uz
        assignments = {}
        items = []
        pairs = []
        clear = True
        predicted = 0.0
        for cell, j, phit in plan["picks"]:
            if str(cell) in assignments:
                continue                      # never two targets on one fibre
            x, y, z = ux[j], uy[j], uz[j]
            a_sum = 0.0
            for (_, zen_k, _, _), moon_k in zip(frames, moons):
                a_sum += site.transmission(x * zen_k[0] + y * zen_k[1] + z * zen_k[2]) * site.lunar_factor(x, y, z, moon_k)
            a = a_sum / 3.0
            unit = cat.flux[j] * teff * a / sv.f0t0
            _, zen_m, north_m, east_m = frames[1]
            alt_j = math.asin(max(-1.0, min(1.0, x * zen_m[0] + y * zen_m[1] + z * zen_m[2]))) * DEG
            az_j = math.atan2(x * east_m[0] + y * east_m[1] + z * east_m[2],
                              x * north_m[0] + y * north_m[1] + z * north_m[2]) * DEG % 360.0
            d = sky.dir_factor(alt_j, az_j) if directional else 1.0
            if d < 0.999:
                clear = False
            row, col = divmod(cell, n)
            items.append((j, row, col, unit, a, d, alt_j, az_j, phit >= 0.999, cat.weight[j],
                          x * north[0] + y * north[1] + z * north[2],
                          x * east[0] + y * east[1] + z * east[2],
                          x * zen[0] + y * zen[1] + z * zen[2]))
            assignments[str(cell)] = cat.ids[j]
            g = unit * rho * d
            pairs.append((cat.weight[j] * (g if g < 1.0 else 1.0) + 1e-6, w_level * a))
            expected = cat.weight[j] * (g if g < 1.0 else 1.0) * sky.expected_multiplier(w_level * a, t) - sv.best[j]
            if expected > 0.0:
                predicted += expected
        program = sky.choose_program(pairs, t)
        self.pending = {"t0": t, "teff": teff, "program": program, "cmd_alt": cmd_alt, "cmd_az": cmd_az,
                        "items": items, "night": night, "clear_dirs": clear, "notices": frozenset(),
                        "predicted": predicted}
        return {"action": "observe", "pointing": {"alt_deg": cmd_alt, "az_deg": cmd_az},
                "assignments": assignments, "duration_seconds": duration, "program": program}

    def status(self, payload: dict) -> str:
        sv = self.sv
        sky = sv.sky
        clock = payload.get("wallclock") or {}
        return (f"{payload.get('now_utc')} obs={sv.observes} total={payload.get('running_total')} "
                f"req_left={sv.required_left} rho={sky.rho:.3f} W={sky.W:.3f} e={sky.e:.2f} price={sv.price:.5f} "
                f"avgT={self.avg_t:.0f} k={self.k:.1f} own={self.own * 1000:.1f}ms eng={self.engine * 1000:.1f}ms "
                f"floor={self.t_floor} miss={self.misses}/{self.assigned} "
                f"gain/pred={self.realised_gain / max(self.predicted_gain, 1e-9):.3f} "
                f"capped={self.planner.stat_negative}/{self.planner.stat_plans} "
                f"cpu_left={clock.get('remaining_real_cpu_seconds')} wall_left={clock.get('wall_remaining_seconds')}")


def safe_wait(payload: dict) -> dict:
    return {"action": "wait", "duration_seconds": 900}


def run(capture) -> int:
    agent = None
    out = sys.stdout
    for line in sys.stdin:
        capture.record(line)
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        kind = message.get("message_type")
        if kind == "initialize":
            try:
                agent = Agent(message["payload"])
            except Exception:  # noqa: BLE001 - never die on initialisation; fall back to waiting
                log("initialize failed:\n" + traceback.format_exc())
                agent = None
            continue
        if kind == "finish":
            if agent is not None:
                log("finish:", json.dumps(message.get("payload", {}), sort_keys=True))
            break
        if kind != "decision_request":
            continue
        arrived = time.monotonic()
        payload = message.get("payload") or {}
        action = None
        if agent is not None:
            if agent.reply_at is not None:
                agent.engine += 0.05 * ((arrived - agent.reply_at) - agent.engine)
            try:
                action = agent.decide(payload)
            except Exception:  # noqa: BLE001 - a planner bug must cost one wait, not the run
                log("decide failed:\n" + traceback.format_exc())
                agent.pending = None
                action = None
            agent.decisions += 1
            if agent.decisions % int(agent.knobs["log_every"]) == 0:
                log(agent.status(payload))
        if action is None:
            action = safe_wait(payload)
        response = {"protocol_version": message.get("protocol_version", PROTOCOL),
                    "message_type": "decision_response",
                    "decision_sequence": message.get("decision_sequence")}
        response.update(action)
        out.write(json.dumps(response, separators=(",", ":")) + "\n")
        out.flush()
        if agent is not None:
            agent.reply_at = time.monotonic()
    return 0


def main() -> int:
    capture = Capture(sys.stderr)
    try:
        return run(capture)
    finally:
        capture.flush()


if __name__ == "__main__":
    raise SystemExit(main())
