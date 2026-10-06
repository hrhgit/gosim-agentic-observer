#!/usr/bin/env python3
"""Write the GitHub Actions minutes each runner organization was billed this month.

The platform estimates an organization's minutes from its jobs (claimed -> finished);
GitHub bills more (each job rounded up, setup time, runs that fail to claim). With
target health on (migration 20261004070000) the monthly limit is checked against
greatest(estimate, billed minutes at the last sync + estimate growth since), so a
stale or failed sync only falls back to the estimate.

Reads billing through the organizer's `gh` login (an organization owner; the GitHub
App has no billing permission) and calls public.observer_set_github_minutes with the
service key. Run it every ~20 minutes (ops/private-target-health.md):

  SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python3 scripts/sync-runner-minutes.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

PREFIX = "AGENTIC-OBSERVER26-runner-"


def billed_minutes(organization: str) -> float:
    out = subprocess.run(["gh", "api", f"/organizations/{organization}/settings/billing/usage/summary"],
                         capture_output=True, text=True, timeout=60, check=True).stdout
    return parse_summary(json.loads(out))


def parse_summary(summary: dict) -> float:
    """Gross Actions minutes of the summary's month (public repositories are not billed)."""
    return float(sum(item.get("grossQuantity") or 0 for item in summary.get("usageItems") or []
                     if str(item.get("product", "")).lower() == "actions"
                     and str(item.get("unitType", "")).lower() == "minutes"))


def rpc(name: str, body: dict):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/rpc/" + name
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"null")


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    organizations = [c["organization"] for c in rpc("observer_runner_configuration", {})
                     if str(c.get("organization", "")).startswith(PREFIX)]
    minutes, failed = {}, []
    for organization in sorted(organizations):
        try:
            minutes[organization] = billed_minutes(organization)
        except Exception as error:  # one organization's failure never blocks the others
            failed.append(organization)
            print(f"{organization}: {type(error).__name__}", file=sys.stderr)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(stamp, json.dumps(minutes, sort_keys=True))
    if minutes and not dry:
        print(stamp, "updated", rpc("observer_set_github_minutes", {"p_minutes": minutes}))
    return 1 if failed and not minutes else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
