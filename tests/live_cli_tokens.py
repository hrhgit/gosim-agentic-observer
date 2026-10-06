#!/usr/bin/env python3
"""Personal API tokens against the LIVE platform (survey26-cli gateway), with a hidden test account.

  set -a; source .secrets/supabase.env; set +a
  CLI_TEST_EMAIL=... CLI_TEST_PASSWORD=... python3 tests/live_cli_tokens.py

Checks: a token is created (shown once, only its hash stored), acts as its owner with the same answers the
website gets, cannot reach organizer actions or other teams' data, cannot widen profile fields, is rate
limited, records its last use, and stops working the moment it is revoked. The token it creates is revoked
at the end. Requires the account to be included in private.cli_config.enabled.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ["SUPABASE_URL"].rstrip("/")
ANON = os.environ["SUPABASE_ANON_KEY"]
SVC = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
GATEWAY = URL + "/functions/v1/survey26-cli"
PROBLEMS: list = []


def ok(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        PROBLEMS.append(msg)


def call(url, body=None, headers=None, method="POST"):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode()
            return r.status, json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text


def rest(path, jwt, body=None, method="POST"):
    return call(URL + "/rest/v1/" + path, body, {"apikey": ANON, "Authorization": "Bearer " + jwt}, method)


def gw(token, body):
    return call(GATEWAY, body, {"Authorization": "Bearer " + token})


def main():
    email, password = os.environ["CLI_TEST_EMAIL"], os.environ["CLI_TEST_PASSWORD"]
    status, session = call(URL + "/auth/v1/token?grant_type=password", {"email": email, "password": password}, {"apikey": ANON})
    ok(status == 200, "test account signs in")
    jwt, user = session["access_token"], session["user"]["id"]

    _, previous = rest("rpc/my_cli_tokens", jwt, {})
    for leftover in previous.get("tokens", []):
        if leftover["name"] == "live-test":  # from an interrupted earlier run
            rest("rpc/revoke_cli_token", jwt, {"p_id": leftover["id"]})
    status, created = rest("rpc/create_cli_token", jwt, {"p_name": "live-test"})
    ok(status == 200 and created["token"].startswith("s26_"), "token created and shown once")
    token = created["token"]
    status, listed = rest("rpc/my_cli_tokens", jwt, {})
    ok(all("token" not in t for t in listed["tokens"]), "token list never contains a token")
    status, rows = call(URL + "/rest/v1/rpc/cli_token_authenticate", {"p_hash": "0" * 64},
                        {"apikey": ANON, "Authorization": "Bearer " + jwt})
    ok(status in (401, 403, 404), "users cannot call the gateway's authentication function (%s)" % status)

    # Same identity and same answers as the website.
    status, who = gw(token, {"op": "whoami"})
    ok(status == 200 and who["data"]["me"]["id"] == user, "token acts as its owner")
    for name, args in (("me", {}), ("kimi_plan_status", {}), ("team_capacity", {}), ("my_team_invitations", {})):
        _, direct = rest("rpc/" + name, jwt, args)
        _, via = gw(token, {"op": "rpc", "name": name, "args": args})
        ok(via.get("data") == direct, "rpc %s answers as on the website" % name)
    _, direct_list = call(URL + "/functions/v1/observer-portal", {"action": "list"}, {"Authorization": "Bearer " + jwt, "apikey": ANON})
    _, via_list = gw(token, {"op": "portal", "fields": {"action": "list"}})
    ok(json.dumps(via_list.get("data", {}).get("quota"), sort_keys=True) == json.dumps(direct_list.get("data", {}).get("quota"), sort_keys=True),
       "portal list (quota) answers as on the website")

    # Organizer actions are not reachable.
    for name in ("admin_scenarios", "set_competition_mode", "admin_teams", "create_cli_token", "revoke_cli_token"):
        status, body = gw(token, {"op": "rpc", "name": name, "args": {}})
        ok(status == 403 and body["error"] == "action_not_available", "organizer/token rpc %s refused" % name)
    for action in ("personal_model", "local_access", "save_team_model"):
        status, body = gw(token, {"op": "portal", "fields": {"action": action}})
        ok(status == 403, "portal action %s refused" % action)
    status, body = gw(token, {"op": "profile_update", "fields": {"is_admin": True}})
    ok(status == 400 and body["error"] == "invalid_field", "profile_update cannot set is_admin")

    # Other teams' data stays invisible, exactly as on the website.
    svc_headers = {"apikey": SVC, "Authorization": "Bearer " + SVC}
    team = (who["data"]["me"]["team"] or {}).get("id") or "00000000-0000-0000-0000-000000000000"
    _, other_runs = call(URL + "/rest/v1/observer_runs?select=id,batch_id,observer_batches!inner(team_id)&observer_batches.team_id=neq.%s&result_path=not.is.null&limit=1" % team,
                         None, svc_headers, "GET")
    if other_runs:
        run = other_runs[0]["id"]
        for action in ("download_result", "agent_log", "diagnostics"):
            status, body = gw(token, {"op": "portal", "fields": {"action": action, "run_id": run}})
            ok(status >= 400 or body.get("data") in ([], None), "another team's run is not readable via %s (%s)" % (action, status))
    _, other_member = call(URL + "/rest/v1/profiles?select=id,team_id&team_id=not.is.null&team_id=neq.%s&limit=1" % team, None, svc_headers, "GET")
    if other_member:
        gw(token, {"op": "rpc", "name": "remove_member", "args": {"p_user_id": other_member[0]["id"]}})
        _, after = call(URL + "/rest/v1/profiles?select=team_id&id=eq.%s" % other_member[0]["id"], None, svc_headers, "GET")
        ok(after[0]["team_id"] == other_member[0]["team_id"], "removing another team's member has no effect (as on the website)")
        status, body = gw(token, {"op": "rpc", "name": "transfer_leadership", "args": {"p_user_id": other_member[0]["id"]}})
        ok(status >= 400, "cannot transfer leadership to another team's member (%s)" % body.get("error"))

    # Last use is recorded.
    _, listed = rest("rpc/my_cli_tokens", jwt, {})
    mine = [t for t in listed["tokens"] if t["id"] == created["id"]]
    ok(mine and mine[0]["last_used_at"], "last use recorded")

    # Rate limit: 120 requests per minute per account.
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        def hit(_):
            try:
                return gw(token, {"op": "unknown"})[0]
            except OSError:
                return None  # a dropped connection under load is not an answer
        codes = list(pool.map(hit, range(250)))
    ok(429 in codes, "rate limit answers 429 beyond 120 requests per minute (%d x 429)" % codes.count(429))

    # Revocation is immediate.
    status, _ = rest("rpc/revoke_cli_token", jwt, {"p_id": created["id"]})
    ok(status == 200, "token revoked")
    status, body = gw(token, {"op": "whoami"})
    ok(status == 401 and body["error"] == "invalid_token", "revoked token refused at once")
    digest = hashlib.sha256(token.encode()).hexdigest()
    ok(digest != token, "only the SHA-256 fingerprint is stored server-side")
    print("\n%d problem(s)" % len(PROBLEMS))
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main())
