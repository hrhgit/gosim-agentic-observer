#!/usr/bin/env python3
"""Supabase project watchdog, run by .github/workflows/db-watchdog.yml.

Within a single run: checks the project up to NUM_CHECKS times ~CHECK_INTERVAL
seconds apart, and if FAIL_THRESHOLD consecutive checks fail, restarts the
project unless it is already restarting, the last restart was too recent, or
the 6h restart cap has been hit. Restart history is kept across runs as a
small JSON file on the dedicated `db-watchdog-state` branch (never main).

No notifications are sent anywhere; this only ever prints to the Actions log.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

MANAGEMENT_API_BASE = "https://api.supabase.com"
ANON_PROBE_PATH = "/rest/v1/announcements?select=id&limit=1"
STATE_BRANCH = "db-watchdog-state"
STATE_FILE = "state.json"

NUM_CHECKS = 4
CHECK_INTERVAL_SECONDS = 60
FAIL_THRESHOLD = 3
RESTART_COOLDOWN_SECONDS = 20 * 60
RESTART_WINDOW_SECONDS = 6 * 60 * 60
RESTART_WINDOW_MAX = 4
CHECK_TIMEOUT = 15


def log(line):
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), line), flush=True)


def http_json(url, headers=None, method="GET", timeout=CHECK_TIMEOUT, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode("utf-8")


def check_management_health(ref, token):
    url = "%s/v1/projects/%s/health?services=db,rest,auth,storage" % (MANAGEMENT_API_BASE, ref)
    status, _ = http_json(url, headers={"Authorization": "Bearer %s" % token})
    return status is not None and 200 <= status < 300, "http_%s" % status


def check_anon_rest(ref, anon_key):
    url = "https://%s.supabase.co%s" % (ref, ANON_PROBE_PATH)
    status, _ = http_json(url, headers={"apikey": anon_key, "Authorization": "Bearer %s" % anon_key})
    return status is not None and 200 <= status < 300, "http_%s" % status


def log_incident_summary(ref, anon_key):
    # Platform-caused preparation/run failures retry on their own (see
    # supabase/migrations/20261003000100_platform_failure_resilience.sql);
    # this only ever logs the backlog here, same as everything else in this
    # script -- the admin incidents page is where an organizer acts on it.
    url = "https://%s.supabase.co/rest/v1/rpc/observer_incident_summary" % ref
    status, raw = http_json(url, headers={"apikey": anon_key, "Authorization": "Bearer %s" % anon_key,
                                           "Content-Type": "application/json"}, method="POST", body={})
    if status is not None and 200 <= status < 300:
        log("incident_summary=%s" % raw.decode("utf-8", "replace"))
    else:
        log("incident_summary_unavailable status=%s" % status)


def get_project_status(ref, token):
    url = "%s/v1/projects/%s" % (MANAGEMENT_API_BASE, ref)
    status, raw = http_json(url, headers={"Authorization": "Bearer %s" % token})
    if status is None or status >= 300:
        return None
    try:
        return json.loads(raw.decode("utf-8")).get("status")
    except (ValueError, UnicodeDecodeError):
        return None


def restart_project(ref, token):
    url = "%s/v1/projects/%s/restart" % (MANAGEMENT_API_BASE, ref)
    status, raw = http_json(url, headers={"Authorization": "Bearer %s" % token}, method="POST")
    ok = status is not None and 200 <= status < 300
    return ok, ("http_%s" % status if status is not None else raw.decode("utf-8", "replace"))


def gh_headers(gh_token):
    return {"Authorization": "Bearer %s" % gh_token, "Accept": "application/vnd.github+json"}


def load_state(repo, gh_token):
    url = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, STATE_FILE, STATE_BRANCH)
    status, raw = http_json(url, headers=gh_headers(gh_token))
    default = {"last_restart_ts": None, "restart_timestamps": []}
    if status == 200:
        payload = json.loads(raw.decode("utf-8"))
        content = base64.b64decode(payload["content"]).decode("utf-8")
        return json.loads(content), payload["sha"]
    log("state_load_warning status=%s (using defaults)" % status)
    return default, None


def save_state(repo, gh_token, state, sha):
    url = "https://api.github.com/repos/%s/contents/%s" % (repo, STATE_FILE)
    content_b64 = base64.b64encode(json.dumps(state).encode("utf-8")).decode("ascii")
    body = {
        "message": "db-watchdog: update restart state",
        "content": content_b64,
        "branch": STATE_BRANCH,
    }
    if sha:
        body["sha"] = sha
    status, raw = http_json(url, headers=gh_headers(gh_token), method="PUT", body=body)
    if status not in (200, 201):
        log("state_save_warning status=%s" % status)


def main():
    dry_run = os.environ.get("DRY_RUN") == "true"
    simulate_fail = os.environ.get("SIMULATE_FAIL") == "true"
    ref = os.environ["SUPABASE_PROJECT_REF"]
    access_token = os.environ["SUPABASE_ACCESS_TOKEN"]
    anon_key = os.environ["SUPABASE_ANON_KEY"]
    gh_token = os.environ["GH_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]

    state, sha = load_state(repo, gh_token)
    now0 = time.time()
    restart_timestamps = [t for t in state.get("restart_timestamps", []) if now0 - t < RESTART_WINDOW_SECONDS]
    last_restart_ts = state.get("last_restart_ts")

    consecutive_fail = 0
    state_changed = False

    for i in range(1, NUM_CHECKS + 1):
        if simulate_fail:
            health_ok, health_detail = False, "simulated"
            rest_ok, rest_detail = False, "simulated"
        else:
            health_ok, health_detail = check_management_health(ref, access_token)
            rest_ok, rest_detail = check_anon_rest(ref, anon_key)

        ok = health_ok and rest_ok
        consecutive_fail = 0 if ok else consecutive_fail + 1
        log(
            "check=%d/%d health_ok=%s(%s) rest_ok=%s(%s) consecutive_fail=%d"
            % (i, NUM_CHECKS, health_ok, health_detail, rest_ok, rest_detail, consecutive_fail)
        )

        if consecutive_fail >= FAIL_THRESHOLD:
            now = time.time()
            cooldown_ok = last_restart_ts is None or (now - last_restart_ts) > RESTART_COOLDOWN_SECONDS
            window_ok = len(restart_timestamps) < RESTART_WINDOW_MAX

            if not cooldown_ok:
                log("action=skip_cooldown last_restart_ts=%s" % last_restart_ts)
            elif not window_ok:
                log("action=skip_restart_cap restarts_in_6h=%d" % len(restart_timestamps))
            else:
                status = "ACTIVE_HEALTHY" if simulate_fail else get_project_status(ref, access_token)
                if status in ("RESTARTING", "COMING_UP"):
                    log("action=skip_already_restarting project_status=%s" % status)
                elif status is None:
                    log("action=skip_status_unknown")
                elif dry_run:
                    log("action=would_restart project=%s (dry run, API not called)" % ref)
                    last_restart_ts = now
                    restart_timestamps = restart_timestamps + [now]
                    state_changed = True
                else:
                    restart_ok, detail = restart_project(ref, access_token)
                    if restart_ok:
                        log("action=restarted project=%s" % ref)
                        last_restart_ts = now
                        restart_timestamps = restart_timestamps + [now]
                        state_changed = True
                    else:
                        log("action=restart_failed detail=%s" % detail)
            break

        if i < NUM_CHECKS:
            time.sleep(CHECK_INTERVAL_SECONDS)
    else:
        log("action=none all_checks_within_threshold")

    if state_changed:
        new_state = {"last_restart_ts": last_restart_ts, "restart_timestamps": restart_timestamps}
        save_state(repo, gh_token, new_state, sha)

    if not simulate_fail:
        log_incident_summary(ref, anon_key)


if __name__ == "__main__":
    sys.exit(main())
