"""Conformance of the two survey26 builds (cli/survey26.py and the Rust binary in cli-rs/).

Every scenario runs against both builds with the same local stand-in of the gateway. Their
--json output must be byte-identical, their exit codes equal, the requests they send equal
(order and body), and the files they write equal. The shared command spec (cli/spec.json)
and error texts (cli/messages.json) are checked against the Python build here as well.

The Rust binary is taken from $SURVEY26_RUST_BIN or cli-rs/target/release/survey26; without
it the Rust half is skipped (the release workflow always builds it first).
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
sys.path.insert(0, str(ROOT / "tests"))
import survey26  # noqa: E402
import cli_spec_introspect  # noqa: E402

TOKEN = "s26_" + "ab" * 32
USER = "11111111-1111-1111-1111-111111111111"
PHASE = "22222222-2222-2222-2222-222222222222"
REV = "33333333-3333-3333-3333-333333333333"
REV2 = "33339999-3333-3333-3333-333333333333"
BATCH = "44444444-4444-4444-4444-444444444444"
BATCH2 = "44449999-4444-4444-4444-444444444444"
RUN_A, RUN_B = "55555555-5555-5555-5555-555555555555", "66666666-6666-6666-6666-666666666666"
SC_A, SC_B = "77777777-7777-7777-7777-777777777777", "88888888-8888-8888-8888-888888888888"
RUST = Path(os.environ.get("SURVEY26_RUST_BIN") or ROOT / "cli-rs" / "target" / "release" / ("survey26.exe" if os.name == "nt" else "survey26"))
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==")


def me(team=True, captain=True):
    return {"id": USER, "email": "u@example.test", "name": "Una", "nickname": "Vega", "avatar_url": None, "city": "Lijiang",
            "seeking": "", "seeking_count": 0, "blurb": "", "contact": "", "show_on_wall": False,
            "team": {"id": "t1", "name": "Stars", "leader_id": USER if captain else "x", "invite_code": "ABCD1234", "max_size": 3,
                     "member_count": 2, "is_locked": False, "project_idea": "idea", "github_repo": ""} if team else None}


def iso(seconds_ago):
    return datetime.fromtimestamp(time.time() - seconds_ago, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00")


def listing(batch_status="scored", evaluated=False, recent=False, repeat=False, no_model=False):
    created = iso(60) if recent else "2026-10-01T00:00:00Z"
    batches = [{"id": BATCH, "status": batch_status, "score": 61.5 if batch_status == "scored" else None,
                "phase_id": PHASE, "revision_id": REV2 if evaluated else "other", "created_at": "2026-10-02T00:00:00Z",
                "repeat_group": "g1" if repeat else None, "repeat_runs": 3 if repeat else None, "quota_refunded": False,
                **({"model_disabled": True} if no_model else {}),
                "observer_runs": [
                    {"id": RUN_B, "scenario_id": SC_B, "status": "scored", "score": 60, "result_path": "x"},
                    {"id": RUN_A, "scenario_id": SC_A, "status": batch_status if batch_status != "scored" else "scored",
                     "score": 63.25, "result_path": "y"}]}]
    if repeat:
        batches.append({"id": BATCH2, "status": "scored", "score": 58.0, "phase_id": PHASE, "revision_id": REV2,
                        "created_at": "2026-10-02T00:00:00Z", "repeat_group": "g1", "repeat_runs": 3, "quota_refunded": True,
                        "observer_runs": [{"id": "r9", "scenario_id": SC_A, "status": "scored", "score": 57.1, "result_path": None}]})
    return {
        "phases": [{"phase_id": PHASE, "projects_enabled": True, "daily_batches": 40,
                    "phases": {"slug": "practice-projects", "name_en": "P", "name_zh": "P", "is_active": True,
                               "starts_at": "2026-09-25 15:17:29.196961+00", "ends_at": None}},
                   {"phase_id": "closed", "projects_enabled": True, "daily_batches": 40,
                    "phases": {"slug": "practice", "is_active": True, "starts_at": None, "ends_at": "2026-10-02 07:22:45.977467+00"}}],
        "projects": [{"id": "p", "title": "Agent", "created_at": created, "observer_revisions": [
            {"id": REV, "status": "reviewable", "approval_digest": "d" * 64, "source_kind": "zip", "source_location": "",
             "manifest": {"run": ["python3", "agent.py"]}, "adapter_files": {"adapter.py": "print('é')"}, "explanation": "ok",
             "error": "", "public_test": {"passed": True, "run_id": RUN_A}, "created_at": created},
            {"id": REV2, "status": "approved", "approval_digest": "e" * 64, "source_kind": "repository",
             "source_location": "https://github.com/o/agent", "manifest": {}, "adapter_files": {}, "explanation": "",
             "error": "", "public_test": {}, "created_at": "2026-09-30T00:00:00Z", "archived_at": None},
            {"id": "3333aaaa-3333-3333-3333-333333333333", "status": "failed", "source_kind": "zip", "error": "build failed",
             "created_at": "2026-09-29T00:00:00Z", "archived_at": "2026-09-30T00:00:00Z"}]}],
        "batches": batches,
        "quota": [{"phase_id": PHASE, "daily_batches": 40, "used": 1, "remaining": 39, "resets_at": "2026-10-05T00:00:00+00:00",
                   "preparations_daily": 40, "preparations_used": 2, "preparations_remaining": 38}],
        "final_versions": [{"phase_id": PHASE, "revision_id": REV2, "source": "best", "deadline": "2026-10-07T15:59:00+00:00", "locked": False}],
    }


def result_zip(wrapper, files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(wrapper + name, content)
    return buf.getvalue()


ENV = {"enabled": True, "open": True, "domains": ["api.kimi.com"], "limits": {"variables": 20},
       "egress_route": {"available": True, "route": "cn", "auto_fallback": True},
       "variables": [{"name": "KIMI_API_KEY", "secret": True, "hint": "wxyz", "value": None, "updated_at": "t"},
                     {"name": "MODEL", "secret": False, "hint": "", "value": "k2", "updated_at": "t", "model": True, "disabled": True}]}
BOARD = {"layout": "cards_overall", "cards": [{"slug": "v4-a", "name": "Card A"}], "scenario": None, "rows": [
    {"rank": 1, "team_id": "other", "team_name": "A", "total_score": 70.5, "submission_count": 3},
    {"rank": 2, "team_id": "t1", "team_name": "Stars", "total_score": 61, "submission_count": 1}]}


FRIENDS = {"uid": 100000123, "daily_limit": 20,
           "friends": [{"user_id": "f1", "uid": 100000007, "name": "Lin 林", "avatar_url": None, "team_id": "t9", "team_name": "Moon",
                        "in_team": True, "since": "2026-10-04T00:00:00Z"},
                       {"user_id": "f2", "uid": 100000042, "name": "Ana", "avatar_url": None, "team_id": None, "team_name": None,
                        "in_team": False, "since": "2026-10-04T01:00:00Z"}],
           "incoming": [{"id": "fr7", "user_id": "u7", "name": "Kai", "avatar_url": None, "created_at": "2026-10-04T02:00:00Z"}],
           "outgoing": [{"id": "fr8", "uid": 100000099, "created_at": "2026-10-04T03:00:00Z"}],
           "blocked": [{"user_id": "u5", "name": "Spam"}]}


def base_routes(url):
    return {
        "whoami": (200, {"data": {"me": me(), "token_id": "t"}}),
        "portal:list": (200, {"data": listing()}),
        "rpc:current_competition": (200, {"data": {"mode": "practice", "phase_id": "x", "project_phase_id": PHASE}}),
        "scenarios": (200, {"data": [{"id": SC_A, "slug": "v4-practice-alpha", "name": "a"},
                                     {"id": SC_B, "slug": "v4-practice-beta", "name": "b"}]}),
        "phases": (200, {"data": [{"id": PHASE, "slug": "practice-projects", "starts_at": None, "ends_at": None, "is_active": True,
                                   "observer_settings": {"projects_enabled": True}},
                                  {"id": "csv", "slug": "practice", "starts_at": None, "ends_at": "2026-10-02", "is_active": True,
                                   "observer_settings": None}]}),
        "rpc:team_members": (200, {"data": [{"id": USER, "name": "Una", "is_leader": True, "github": "una"},
                                            {"id": "m2", "name": "Bo", "is_leader": False, "github": None}]}),
        "portal:team_environment": (200, {"data": {"team_environment": ENV}}),
        "portal:save_team_variable": (200, {"data": {"team_environment": ENV}}),
        "portal:delete_team_variable": (200, {"data": {"team_environment": ENV}}),
        "portal:set_team_variable_flags": (200, {"data": {"team_environment": ENV}}),
        "portal:set_team_domains": (200, lambda b: (200, {"data": {"team_environment": dict(ENV, domains=b["fields"]["domains"])}})),
        "portal:set_team_egress_route": (200, lambda b: (200, {"data": {"team_environment": dict(ENV, egress_route={
            "available": True, "route": b["fields"]["route"],
            "auto_fallback": b["fields"].get("auto_fallback", ENV["egress_route"]["auto_fallback"])})}})),
        "portal:evaluate": (200, {"data": {"batch_id": BATCH}}),
        "portal:approve": (200, {"data": {"accepted": True}}),
        "portal:withdraw": (200, {"data": {"accepted": True}}),
        "portal:evidence": (200, {"data": {"accepted": True}}),
        "portal:diagnostics": (200, {"data": [{"kind": "prepare", "status": "succeeded", "code": "succeeded", "log": "built",
                                               "finished_at": "2026-10-01T00:01:00Z"}]}),
        "portal:agent_log": (200, lambda b: (200, {"data": {"available": True, "bytes": 21, "truncated": not b["fields"].get("full"),
                                                            "log": "line 1\nline 2\r\nline 3\n"}})),
        "portal:download_result": (200, lambda b: (200, {"data": {"url": url + ("/a.zip" if b["fields"]["run_id"] == RUN_A else "/b.zip")}})),
        "portal:download_project": (200, {"data": {"url": url + "/project.zip"}}),
        "portal:set_final_version": (200, lambda b: (200, {"data": {"final_version": {"phase_id": PHASE, "revision_id": b["fields"]["revision_id"],
                                                                                    "source": "chosen" if b["fields"]["revision_id"] else "best"}}})),
        "portal:upload": (200, {"data": {"id": "u1", "path": "p/source.zip", "token": "t", "upload_url": url + "/upload?token=t", "apikey": "anon"}}),
        "portal:submit_zip": (200, {"data": {"revision_id": REV}}),
        "portal:submit_repository": (200, {"data": {"revision_id": REV, "source_commit": "c" * 40}}),
        "rpc:observer_create_repeat_batches": (200, {"data": ["a", "b", "c"]}),
        "rpc:observer_card_board": (200, {"data": BOARD}),
        "rpc:leaderboard": (200, {"data": [{"rank": 1, "team_id": "t1", "team_name": "Stars", "total_score": 21119.494551, "submission_count": 6}]}),
        "rpc:participants_wall": (200, {"data": [{"id": "w1", "name": "Wei", "seeking": "ai", "looking_for_team": True, "team_name": None, "blurb": "hi"},
                                                 {"id": "w2", "name": "Li", "seeking": "", "looking_for_team": False, "team_name": "X", "blurb": ""}]}),
        "rpc:teammate_contact": (200, {"data": {"contact": "wx: abc", "github": "li"}}),
        "rpc:team_directory": (200, {"data": [{"id": "d1", "name": "Open", "member_count": 1, "max_size": 3, "project_idea": ""}]}),
        "rpc:team_capacity": (200, {"data": {"full": False, "limit": 100, "teams": 50, "remaining": 50}}),
        "rpc:my_team_invitations": (200, {"data": [
            {"id": "i1", "direction": "received", "kind": "invite", "status": "pending", "team_name": "Stars", "updated_at": "2026-10-03T00:00:00Z"},
            {"id": "i2", "direction": "received", "kind": "request", "status": "pending", "team_name": "Stars", "updated_at": "2026-10-04T00:00:00Z"},
            {"id": "i3", "direction": "sent", "kind": "request", "status": "pending", "team_name": "Moon", "updated_at": "2026-10-05T00:00:00Z"}]}),
        "rpc:kimi_plan_status": (200, {"data": {"has_team": True, "is_captain": True, "eligible": True, "imported": True, "available": 3,
                                                "code": "KIMI-1", "claimed_at": "2026-10-04T00:00:00Z"}}),
        "rpc:claim_kimi_plan_code": (200, {"data": {"already": False}}),
        "rpc:redeem_providers": (200, {"data": [{"provider": "acme", "available": 2, "claimed_by_my_team": True}]}),
        "rpc:my_redeem_codes": (200, {"data": [{"provider": "acme", "code": "AC-1", "note": "n"}]}),
        "rpc:claim_redeem_code": (200, {"data": {"provider": "acme", "code": "AC-1", "note": "", "already": True}}),
        "rpc:my_friends": (200, {"data": FRIENDS}),
        "rpc:send_friend_request": (200, {"data": {"status": "sent", "request_id": "fr1"}}),
        "rpc:send_team_invite_by_uid": (200, {"data": {"status": "sent", "invitation_id": "inv9"}}),
        "rpc:respond_friend_request": (200, {"data": "accepted"}),
        "rpc:cancel_friend_request": (200, {"data": None}),
        "rpc:remove_friend": (200, {"data": None}),
        "rpc:block_user": (200, {"data": None}),
        "rpc:unblock_user": (200, {"data": None}),
        "profile_update": (200, {"data": {"me": me()}}),
        "avatar_upload": (200, {"data": {"avatar_url": url + "/avatar.png"}}),
        "avatar_clear": (200, {"data": {"avatar_url": None}}),
    }


class Gateway:
    def __init__(self):
        self.requests, self.routes, self.files, self.puts = [], {}, {}, []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                content = gateway.files.get(self.path.split("?")[0])
                self.send_response(200 if content is not None else 404)
                self.send_header("content-length", str(len(content or b"")))
                self.end_headers()
                self.wfile.write(content or b"")

            def do_PUT(self):
                body = self.rfile.read(int(self.headers["content-length"]))
                gateway.puts.append({"path": self.path, "apikey": self.headers.get("apikey"),
                                     "type": self.headers.get("content-type", "").split(";")[0], "zip": b"agent.py" in body})
                self.send_response(200)
                self.send_header("content-length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                gateway.requests.append(body)
                op = body["op"]
                key = op + ":" + (body.get("name") or (body.get("fields") or {}).get("action") or "")
                status, payload = gateway.routes.get(key, gateway.routes.get(op, (400, {"error": "unknown_operation"})))
                if callable(payload):
                    status, payload = payload(body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def builds():
    yield "python", [sys.executable, str(ROOT / "cli" / "survey26.py")]
    if RUST.exists():
        yield "rust", [str(RUST)]


def snapshot_files(folder: Path):
    files = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name not in ("config.json",):
            data = path.read_bytes()
            if path.suffix == ".zip" and zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as z:
                    files[path.relative_to(folder).as_posix()] = {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}
            else:
                files[path.relative_to(folder).as_posix()] = data.decode("utf-8", "replace")
    return files


def run_build(command, gw, tmp_path, argv, routes=None, stdin="", files=None, env=None, token=True, config=None):
    gw.requests.clear()
    gw.puts.clear()
    gw.routes = dict(base_routes(gw.url), **(routes or {}))
    gw.files = {"/a.zip": result_zip("runner-abc123/", {"result.json": "{}", "decisions.csv": "x"}),
                "/b.zip": result_zip("", {"result.json": "{\"b\": 1}", "agent.log": "log"}),
                "/project.zip": b"PK-project"}
    work = tmp_path / ("run-%d" % len(list(tmp_path.iterdir())))
    work.mkdir()
    for name, content in (files or {}).items():
        (work / name).write_bytes(content)
    if config is not None:
        (work / "cfg").mkdir()
        (work / "cfg" / "config.json").write_text(json.dumps(config))
    clean = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy") and not k.startswith(("SURVEY26_", "LANG", "LC_"))}
    clean.update({"SURVEY26_API": gw.url, "SURVEY26_LANG": "en", "SURVEY26_SLEEP_SCALE": "0", "SURVEY26_CONFIG": "cfg/config.json",
                  "NO_PROXY": "*", "HOME": str(work)})
    if token:
        clean["SURVEY26_TOKEN"] = TOKEN
    clean.update(env or {})
    done = subprocess.run(command + argv, input=stdin.encode(), capture_output=True, cwd=work, env=clean, timeout=120)
    written = snapshot_files(work)
    config_after = json.loads((work / "cfg" / "config.json").read_text()) if (work / "cfg" / "config.json").exists() else None
    return {"exit": done.returncode, "stdout": done.stdout.decode("utf-8"), "requests": list(gw.requests),
            "puts": list(gw.puts), "files": written, "config": config_after, "stderr": done.stderr.decode("utf-8", "replace")}


@pytest.fixture(scope="module")
def gw():
    g = Gateway()
    yield g
    g.server.shutdown()


def recent_listing():
    return (200, {"data": listing(recent=True)})


# (name, argv, options) — options: routes, stdin, files, env, token, config, exit (expected exit code)
SCENARIOS = [
    ("whoami", ["--json", "whoami"], {"exit": 0}),
    ("whoami-human", ["whoami"], {"exit": 0, "human": True}),
    ("no-token", ["--json", "whoami"], {"token": False, "exit": 3}),
    ("bad-token", ["--json", "--token", "eyJ.x.y", "whoami"], {"exit": 3}),
    ("token-from-config", ["--json", "whoami"], {"token": False, "config": {"token": TOKEN}, "exit": 0}),
    ("daily-limit", ["--json", "whoami"], {"routes": {"whoami": (400, {"error": "daily_limit"})}, "exit": 8}),
    ("daily-limit-zh", ["--json", "--lang", "zh", "whoami"], {"routes": {"whoami": (400, {"error": "daily_limit"})}, "exit": 8}),
    ("rate-limited", ["--json", "whoami"], {"routes": {"whoami": (429, {"error": "rate_limited"})}, "exit": 5}),
    ("unknown-code", ["--json", "whoami"], {"routes": {"whoami": (400, {"error": "some_new_code"})}, "exit": 1}),
    ("not-json", ["--json", "whoami"], {"routes": {"whoami": (200, {"nodata": 1})}, "exit": 1}),
    ("read-retried", ["--json", "whoami"], {"routes": {"whoami": (503, {"error": "gateway_unavailable"})}, "exit": 6}),
    ("write-not-retried", ["--json", "team", "join", "abcd1234"], {"routes": {"rpc:join_team": (503, {"error": "gateway_unavailable"})}, "exit": 6}),
    ("fk-message", ["--json", "team", "disband", "--yes"],
     {"routes": {"rpc:disband_team": (400, {"error": 'update or delete on table "teams" violates foreign key constraint "x" on table "observer_projects"'})}, "exit": 1}),
    ("login-logout", ["--json", "login", "--token-stdin"], {"token": False, "stdin": TOKEN + "\n", "exit": 0}),
    ("login-no-token", ["--json", "login"], {"token": False, "exit": 2}),
    ("logout", ["--json", "logout"], {"config": {"token": TOKEN, "other": 1}, "exit": 0}),
    ("profile-show", ["--json", "profile", "show"], {"exit": 0}),
    ("profile-set", ["--json", "profile", "set", "--nickname", " Vega ", "--github", " @vega", "--astro-level", "3", "--blurb", "x" * 200], {"exit": 0}),
    ("profile-set-nothing", ["--json", "profile", "set"], {"exit": 2}),
    ("profile-set-long-nick", ["--json", "profile", "set", "--nickname", "界" * 41], {"exit": 1}),
    ("avatar-set", ["--json", "profile", "avatar", "set", "a.png"], {"files": {"a.png": PNG}, "exit": 0}),
    ("avatar-bad", ["--json", "profile", "avatar", "set", "a.txt"], {"files": {"a.txt": b"hello"}, "exit": 1}),
    ("avatar-missing", ["--json", "profile", "avatar", "set", "nope.png"], {"exit": 2}),
    ("avatar-clear", ["--json", "profile", "avatar", "clear"], {"exit": 0}),
    ("teammates-list", ["--json", "teammates", "list", "--looking", "--limit", "5"], {"exit": 0}),
    ("teammates-contact", ["--json", "teammates", "contact", "w2"], {"exit": 0}),
    ("teammates-invite", ["--json", "teammates", "invite", "w1"], {"routes": {"rpc:send_team_invite": (200, {"data": "inv1"})}, "exit": 0}),
    ("visibility-show", ["--json", "teammates", "visibility", "show"], {"exit": 0}),
    ("visibility-on", ["--json", "teammates", "visibility", "on", "--blurb", "  hello  ", "--seeking", "ai"], {"exit": 0}),
    ("visibility-off", ["--json", "teammates", "visibility", "off", "--seeking", ""], {"exit": 0}),
    ("team-show", ["--json", "team", "show"], {"exit": 0}),
    ("team-show-none", ["--json", "team", "show"], {"routes": {"whoami": (200, {"data": {"me": me(team=False)}})}, "exit": 0}),
    ("team-members", ["--json", "team", "members"], {"exit": 0}),
    ("team-create", ["--json", "team", "create", " Stars ", "--max-size", "2", "--idea", " x "], {"routes": {"rpc:create_team": (200, {"data": "t1"})}, "exit": 0}),
    ("team-create-bad-size", ["--json", "team", "create", "Stars", "--max-size", "5"], {"exit": 2}),
    ("team-join", ["--json", "team", "join", " abcd1234 "], {"routes": {"rpc:join_team": (200, {"data": None})}, "exit": 0}),
    ("team-leave-needs-yes", ["--json", "team", "leave"], {"exit": 2}),
    ("team-leave", ["--json", "team", "leave", "-y"], {"routes": {"rpc:leave_team": (200, {"data": None})}, "exit": 0}),
    ("team-set", ["--json", "team", "set", "--name", "Stars", "--lock", "--repo", "https://github.com/o/r"], {"routes": {"rpc:update_team": (200, {"data": None})}, "exit": 0}),
    ("team-set-unlock", ["--json", "team", "set", "--unlock", "--max-size=2"], {"routes": {"rpc:update_team": (200, {"data": None})}, "exit": 0}),
    ("team-code", ["--json", "team", "code", "--regenerate"], {"routes": {"rpc:regenerate_invite_code": (200, {"data": "NEW"})}, "exit": 0}),
    ("team-transfer", ["--json", "team", "transfer", "m2", "--yes"], {"routes": {"rpc:transfer_leadership": (200, {"data": None})}, "exit": 0}),
    ("team-kick", ["--json", "team", "kick", "m2", "--yes"], {"routes": {"rpc:remove_member": (200, {"data": None})}, "exit": 0}),
    ("team-disband", ["--json", "team", "disband", "--yes"], {"routes": {"rpc:disband_team": (200, {"data": None})}, "exit": 0}),
    ("team-directory", ["--json", "team", "directory"], {"exit": 0}),
    ("team-request", ["--json", "team", "request", "d1"], {"routes": {"rpc:request_team_join": (400, {"error": "team_unavailable"})}, "exit": 1}),
    ("team-capacity", ["--json", "team", "capacity"], {"exit": 0}),
    ("invites-list", ["--json", "invites", "list"], {"routes": {"rpc:mark_team_invitations_read": (200, {"data": 2})}, "exit": 0}),
    ("invites-keep", ["--json", "invites", "list", "--keep-unread"], {"exit": 0}),
    ("invites-accept", ["--json", "invites", "accept", "i1"], {"routes": {"rpc:respond_team_invite": (200, {"data": None})}, "exit": 0}),
    ("invites-decline", ["--json", "invites", "decline", "i1"], {"routes": {"rpc:respond_team_invite": (200, {"data": None})}, "exit": 0}),
    ("invites-cancel", ["--json", "invites", "cancel", "i3"], {"routes": {"rpc:cancel_team_invite": (200, {"data": None})}, "exit": 0}),
    ("whoami-uid", ["--json", "whoami"], {"routes": {"whoami": (200, {"data": {"me": dict(me(), uid=100000123)}})}, "exit": 0}),
    ("friends-list", ["--json", "friends", "list"], {"exit": 0}),
    ("friends-list-human", ["friends", "list"], {"exit": 0, "human": True}),
    ("friends-list-empty", ["--json", "friends", "list"], {"routes": {"rpc:my_friends": (200, {"data": None})}, "exit": 0}),
    ("friends-add", ["--json", "friends", "add", " 100000042 "], {"exit": 0}),
    ("friends-add-accepted", ["--json", "--lang", "zh", "friends", "add", "100000042"],
     {"routes": {"rpc:send_friend_request": (200, {"data": {"status": "accepted"}})}, "exit": 0}),
    ("friends-add-bad-uid", ["--json", "friends", "add", "12345"], {"exit": 2}),
    ("friends-add-leading-zero", ["--json", "friends", "add", "012345678"], {"exit": 2}),
    ("friends-add-self", ["--json", "friends", "add", "100000123"], {"routes": {"rpc:send_friend_request": (200, {"data": {"error": "self"}})}, "exit": 1}),
    ("friends-add-unavailable", ["--json", "friends", "add", "100000124"],
     {"routes": {"rpc:send_friend_request": (200, {"data": {"error": "uid_unavailable"}})}, "exit": 4}),
    ("friends-add-limit", ["--json", "--lang", "zh", "friends", "add", "100000124"],
     {"routes": {"rpc:send_friend_request": (200, {"data": {"error": "daily_limit"}})}, "exit": 8}),
    ("friends-accept", ["--json", "friends", "accept", "fr7"], {"exit": 0}),
    ("friends-decline", ["--json", "friends", "decline", "fr7"], {"routes": {"rpc:respond_friend_request": (200, {"data": "declined"})}, "exit": 0}),
    ("friends-accept-wrong", ["--json", "friends", "accept", "fr8"],
     {"routes": {"rpc:respond_friend_request": (400, {"error": "request_not_found"})}, "exit": 4}),
    ("friends-cancel", ["--json", "friends", "cancel", "fr8"], {"exit": 0}),
    ("friends-remove", ["--json", "friends", "remove", "f1"], {"exit": 0}),
    ("friends-remove-not", ["--json", "friends", "remove", "x"], {"routes": {"rpc:remove_friend": (400, {"error": "not_friends"})}, "exit": 1}),
    ("friends-block", ["--json", "friends", "block", "u7"], {"exit": 0}),
    ("friends-unblock", ["--json", "friends", "unblock", "u5"], {"exit": 0}),
    ("team-invite-uid", ["--json", "team", "invite-uid", "100000042"], {"exit": 0}),
    ("team-invite-uid-in-team", ["--json", "team", "invite-uid", "100000007"],
     {"routes": {"rpc:send_team_invite_by_uid": (200, {"data": {"error": "recipient_in_team"}})}, "exit": 1}),
    ("team-invite-uid-unknown", ["--json", "team", "invite-uid", "999999999"],
     {"routes": {"rpc:send_team_invite_by_uid": (200, {"data": {"error": "uid_not_found"}})}, "exit": 4}),
    ("team-invite-uid-leader", ["--json", "team", "invite-uid", "100000042"],
     {"routes": {"rpc:send_team_invite_by_uid": (200, {"data": {"error": "leader_only"}})}, "exit": 1}),
    ("team-invite-uid-bad", ["--json", "team", "invite-uid", "abc"], {"exit": 2}),
    ("env-show", ["--json", "env", "show"], {"exit": 0}),
    ("env-show-human", ["env", "show"], {"exit": 0, "human": True}),
    ("env-set-stdin", ["--json", "env", "set", "KIMI_API_KEY", "--value-stdin"], {"stdin": "sk-secret-wxyz\n\n", "exit": 0}),
    ("env-set-from-env", ["--json", "env", "set", "MODEL", "--from-env", "MY_MODEL", "--plain"], {"env": {"MY_MODEL": "k2"}, "exit": 0}),
    ("env-set-two-sources", ["--json", "env", "set", "X", "v", "--value-stdin"], {"exit": 2}),
    ("env-set-no-value", ["--json", "env", "set", "X"], {"exit": 2}),
    ("env-unset", ["--json", "env", "unset", "MODEL"], {"exit": 0}),
    ("env-disable", ["--json", "env", "disable", "MODEL"], {"exit": 0}),
    ("env-enable-human-zh", ["--lang", "zh", "env", "enable", "MODEL"], {"exit": 0, "human": True}),
    ("env-tag-model", ["--json", "env", "tag", "KIMI_API_KEY", "model"], {"exit": 0}),
    ("env-tag-none-human", ["env", "tag", "MODEL", "none"], {"exit": 0, "human": True}),
    ("env-tag-bad", ["--json", "env", "tag", "MODEL", "maybe"], {"exit": 2}),
    ("env-disable-missing", ["--json", "env", "disable", "NOPE"],
     {"routes": {"portal:set_team_variable_flags": (400, {"error": "team_variable_not_found"})}, "exit": 1}),
    ("env-domains", ["--json", "env", "domains"], {"exit": 0}),
    ("env-domains-set", ["--json", "env", "domains", "set", "a.example", "b.example"], {"exit": 0}),
    ("env-domains-clear", ["--json", "env", "domains", "clear"], {"exit": 0}),
    ("env-route-show", ["--json", "env", "route"], {"exit": 0}),
    ("env-route-show-human-zh", ["--lang", "zh", "env", "route"], {"exit": 0, "human": True}),
    ("env-route-set", ["--json", "env", "route", "overseas"], {"exit": 0}),
    ("env-route-set-no-fallback-human", ["env", "route", "cn", "--no-fallback"], {"exit": 0, "human": True}),
    ("env-route-fallback-only", ["--json", "env", "route", "--fallback"], {"exit": 0}),
    ("env-route-direct", ["--json", "env", "route", "direct"], {"exit": 0}),
    ("env-route-bad", ["--json", "env", "route", "proxy"], {"exit": 2}),
    ("env-model-kimi", ["--json", "env", "model", "--provider", "kimi", "--key", "sk-kimi-123456789"], {"exit": 0}),
    ("env-model-human", ["env", "model", "--provider", "deepseek", "--key", "sk-1"], {"exit": 0, "human": True}),
    ("env-model-key-stdin", ["--json", "env", "model", "--provider", "moonshot", "--key", "-", "--model", " kimi-k3 "], {"stdin": "  sk-stdin-key \n", "exit": 0}),
    ("env-model-taken", ["--json", "env", "model", "--provider", "kimi", "--prefix", "kimi", "--key", "sk-1"], {"exit": 2}),
    ("env-model-replace", ["--json", "env", "model", "--provider", "kimi", "--prefix", " kimi ", "--key", "sk-1", "--replace"], {"exit": 0}),
    ("env-model-prefix-normalized", ["--json", "env", "model", "--provider", "zhipu", "--prefix", "9-glm.x", "--key", "sk-1"], {"exit": 0}),
    ("env-model-prefix-empty", ["--json", "env", "model", "--provider", "zhipu", "--prefix", "123", "--key", "sk-1"], {"exit": 2}),
    ("env-model-anthropic", ["--json", "env", "model", "--provider", "anthropic", "--key", "sk-ant"], {"exit": 0}),
    ("env-model-custom-no-url", ["--json", "env", "model", "--provider", "custom", "--key", "sk-1"], {"exit": 2}),
    ("env-model-custom", ["--json", "env", "model", "--provider", "custom", "--key", "sk-1", "--base-url", "https://llm.example/v1", "--model", "m1"], {"exit": 0}),
    ("env-model-http-url", ["--json", "env", "model", "--provider", "openai", "--key", "sk-1", "--base-url", "http://llm.example"], {"exit": 2}),
    ("env-model-empty-key", ["--json", "env", "model", "--provider", "openai", "--key", "-"], {"stdin": "\n", "exit": 2}),
    ("env-model-drops-old-model", ["--json", "env", "model", "--provider", "openai", "--key", "sk-1", "--replace"],
     {"routes": {"portal:team_environment": (200, {"data": {"team_environment": dict(ENV, variables=ENV["variables"] + [
         {"name": "OPENAI_MODEL", "secret": False, "hint": "", "value": "old", "updated_at": "t"}])}})}, "exit": 0}),
    ("env-model-limit", ["--json", "env", "model", "--provider", "kimi", "--key", "sk-1"],
     {"routes": {"portal:team_environment": (200, {"data": {"team_environment": dict(ENV, limits={"variables": 3})}})}, "exit": 8}),
    ("env-model-missing-key", ["env", "model", "--provider", "kimi"], {"exit": 2, "human": True}),
    ("env-model-bad-provider", ["env", "model", "--provider", "nope", "--key", "x"], {"exit": 2, "human": True}),
    ("project-list", ["--json", "project", "list"], {"exit": 0}),
    ("project-list-all", ["--json", "project", "list", "--all"], {"exit": 0}),
    ("project-show", ["--json", "project", "show", "33333333", "--files"], {"exit": 0}),
    ("project-show-human", ["project", "show", "33333333"], {"exit": 0, "human": True}),
    ("project-show-ambiguous", ["--json", "project", "show", "3333"], {"exit": 2}),
    ("project-show-short", ["--json", "project", "show", "333"], {"exit": 2}),
    ("project-show-missing", ["--json", "project", "show", "abcdef"], {"exit": 4}),
    ("project-confirm-needs-yes", ["--json", "project", "confirm", "33333333"], {"exit": 2}),
    ("project-confirm", ["--json", "project", "confirm", "33333333", "--yes"], {"exit": 0}),
    ("project-confirm-wrong", ["--json", "project", "confirm", "33339999", "--yes"], {"exit": 1}),
    ("project-wait-ok", ["--json", "project", "wait", "33339999"], {"exit": 0}),
    ("project-wait-failed", ["--json", "project", "wait", "3333aaaa"], {"exit": 9}),
    ("project-logs", ["--json", "project", "logs", "33333333", "--full"], {"exit": 0}),
    ("project-withdraw", ["--json", "project", "withdraw", "33333333", "--yes"], {"exit": 0}),
    ("project-download", ["--json", "project", "download", "33333333"], {"exit": 0}),
    ("project-download-o", ["--json", "project", "download", "33333333", "-o", "p.zip"], {"exit": 0}),
    ("project-evidence", ["--json", "project", "evidence", "33333333", "--notes", "-", "--code-url", "https://x"], {"stdin": "notes\n", "exit": 0}),
    ("submit-repo", ["--json", "project", "submit-repo", "https://github.com/o/agent.git/", "--title", "Agent"], {"exit": 0}),
    ("submit-repo-duplicate", ["--json", "project", "submit-repo", "https://github.com/o/new"], {"routes": {"portal:list": recent_listing()}, "exit": 0}),
    ("submit-repo-branch-subdir", ["--json", "project", "submit-repo", "https://github.com/o/agent", "--branch", " dev/x ", "--subdir", "apps/agent"],
     {"routes": {"portal:submit_repository": (200, {"data": {"revision_id": REV, "source_commit": "c" * 40, "source_ref": "dev/x",
                                                              "source_subdir": "apps/agent"}})}, "exit": 0}),
    ("submit-repo-branch-human", ["project", "submit-repo", "https://github.com/o/agent/tree/dev/apps/agent"],
     {"routes": {"portal:submit_repository": (200, {"data": {"revision_id": REV, "source_commit": "c" * 40, "source_ref": "dev",
                                                              "source_subdir": "apps/agent"}})}, "exit": 0, "human": True}),
    ("submit-repo-ref-missing", ["--json", "project", "submit-repo", "https://github.com/o/agent", "--branch", "nope"],
     {"routes": {"portal:submit_repository": (400, {"error": "source_ref_not_found"})}, "exit": 4}),
    ("upload", ["--json", "project", "upload", "agent.zip"], {"files": {"agent.zip": result_zip("", {"agent.py": "print(1)"})}, "exit": 0}),
    ("upload-duplicate", ["--json", "project", "upload", "agent.zip", "--title", "Agent"],
     {"files": {"agent.zip": result_zip("", {"agent.py": "print(1)"})}, "routes": {"portal:list": recent_listing()}, "exit": 2}),
    ("upload-not-zip", ["--json", "project", "upload", "x.zip"], {"files": {"x.zip": b"hello"}, "exit": 1}),
    ("upload-wrong-ext", ["--json", "project", "upload", "x.tar"], {"files": {"x.tar": b"hello"}, "exit": 1}),
    ("upload-not-finished", ["--json", "project", "upload", "agent.zip"],
     {"files": {"agent.zip": result_zip("", {"agent.py": "1"})}, "routes": {"portal:submit_zip": (400, {"error": "upload_not_finished"})}, "exit": 1}),
    ("eval-start", ["--json", "eval", "start", REV2], {"exit": 0}),
    ("eval-start-phase", ["--json", "eval", "start", REV2, "--phase", "practice"], {"exit": 1}),
    ("eval-repeat-needs-yes", ["--json", "eval", "start", "33339"], {"routes": {"portal:list": (200, {"data": listing(evaluated=True)})}, "exit": 2}),
    ("eval-repeat", ["--json", "eval", "start", "33339", "--yes"], {"routes": {"portal:list": (200, {"data": listing(evaluated=True)})}, "exit": 0}),
    ("eval-already", ["--json", "eval", "start", REV2, "--yes"],
     {"routes": {"portal:evaluate": (200, lambda b: (200, {"data": {"batch_id": "b2"}}) if b["fields"].get("confirm_repeat")
                                     else (400, {"error": "revision_already_evaluated"}))}, "exit": 0}),
    ("eval-no-phase", ["--json", "eval", "start", REV2], {"routes": {"rpc:current_competition": (200, {"data": {"mode": "competition", "phase_id": "zz"}}),
                                                                     "rpc:my_observer_phase": (200, {"data": None})}, "exit": 1}),
    ("eval-selfcheck", ["--json", "eval", "selfcheck", REV2, "--yes"], {"exit": 0}),
    ("eval-start-no-model", ["--json", "eval", "start", REV2, "--no-model"], {"exit": 0}),
    ("eval-start-no-model-human", ["eval", "start", REV2, "--no-model"], {"exit": 0, "human": True}),
    ("eval-repeat-no-model", ["--json", "eval", "start", "33339", "--yes", "--no-model"],
     {"routes": {"portal:list": (200, {"data": listing(evaluated=True)})}, "exit": 0}),
    ("eval-selfcheck-no-model", ["--json", "eval", "selfcheck", REV2, "--yes", "--no-model"], {"exit": 0}),
    ("eval-no-model-refused", ["--json", "eval", "start", REV2, "--no-model"],
     {"routes": {"portal:evaluate": (400, {"error": "no_model_not_available"})}, "exit": 1}),
    ("eval-list-no-model", ["--json", "eval", "list"], {"routes": {"portal:list": (200, {"data": listing(no_model=True)})}, "exit": 0}),
    ("eval-show-no-model-human", ["eval", "show", "latest"], {"routes": {"portal:list": (200, {"data": listing(no_model=True)})}, "exit": 0, "human": True}),
    ("eval-selfcheck-needs-yes", ["--json", "eval", "selfcheck", REV2], {"exit": 2}),
    ("eval-list", ["--json", "eval", "list", "--limit", "1"], {"routes": {"portal:list": (200, {"data": listing(repeat=True)})}, "exit": 0}),
    ("eval-list-negative", ["--json", "eval", "list", "--limit", "-1"], {"routes": {"portal:list": (200, {"data": listing(repeat=True)})}, "exit": 0}),
    ("eval-show-selfcheck", ["--json", "eval", "show", "latest"], {"routes": {"portal:list": (200, {"data": listing(repeat=True)})}, "exit": 0}),
    ("eval-show-human", ["eval", "show", "latest"], {"routes": {"portal:list": (200, {"data": listing(repeat=True)})}, "exit": 0, "human": True}),
    ("eval-wait", ["--json", "eval", "wait"], {"exit": 0}),
    ("eval-wait-failed", ["--json", "eval", "wait", "44444444"], {"routes": {"portal:list": (200, {"data": listing("failed")})}, "exit": 9}),
    ("eval-wait-timeout", ["--json", "eval", "wait", "--timeout", "0"], {"routes": {"portal:list": (200, {"data": listing("running")})}, "exit": 7}),
    ("eval-wait-none", ["--json", "eval", "wait"], {"routes": {"portal:list": (200, {"data": {"batches": []}})}, "exit": 4}),
    ("results-show", ["--json", "results", "show", "44444444"], {"exit": 0}),
    ("results-log-tail", ["--json", "results", "log", "55555555", "--tail", "2"], {"exit": 0}),
    ("results-log-negative-tail", ["--json", "results", "log", "55555555", "--tail", "-1", "--agent-only"], {"exit": 0}),
    ("results-log-out", ["--json", "results", "log", "55555555", "-o", "agent.log"], {"exit": 0}),
    ("results-log-unknown-uuid", ["--json", "results", "log", "99999999-9999-9999-9999-999999999999"], {"exit": 0}),
    ("results-download", ["--json", "results", "download", "55555555"], {"exit": 0}),
    ("results-download-all", ["--json", "results", "download-all"], {"exit": 0}),
    ("results-download-all-no-model", ["--json", "results", "download-all"],
     {"routes": {"portal:list": (200, {"data": listing(no_model=True)})}, "exit": 0}),
    ("results-download-all-partial", ["--json", "results", "download-all", "latest", "-o", "all.zip"],
     {"routes": {"portal:download_result": (200, lambda b: (200, {"data": {"url": "http://127.0.0.1:1/x"}}) if b["fields"]["run_id"] == RUN_B
                                            else (404, {"error": "result_not_ready"}))}, "exit": 1}),
    ("final-show", ["--json", "final", "show"], {"exit": 0}),
    ("final-show-none", ["--json", "final", "show"], {"routes": {"portal:list": (200, {"data": dict(listing(), final_versions=[])})}, "exit": 0}),
    ("final-set", ["--json", "final", "set", "33339999"], {"exit": 0}),
    ("final-set-none", ["--json", "final", "set", "33339999"], {"routes": {"portal:list": (200, {"data": dict(listing(), final_versions=None)})}, "exit": 1}),
    ("final-clear-needs-yes", ["--json", "final", "clear"], {"exit": 2}),
    ("final-clear", ["--json", "final", "clear", "--yes"], {"exit": 0}),
    ("quota", ["--json", "quota"], {"exit": 0}),
    ("quota-human", ["quota"], {"exit": 0, "human": True}),
    ("quota-human-zh", ["--lang", "zh", "quota"], {"exit": 0, "human": True}),
    ("competition", ["--json", "competition"], {"exit": 0}),
    ("leaderboard", ["--json", "leaderboard"], {"exit": 0}),
    ("leaderboard-mine", ["--json", "leaderboard", "--phase", "practice-projects", "--mine", "--card", "v4-a"], {"exit": 0}),
    ("leaderboard-csv", ["--json", "leaderboard", "--phase", "practice", "--limit", "2"], {"exit": 0}),
    ("leaderboard-missing", ["--json", "leaderboard", "--phase", "nope"], {"exit": 4}),
    ("kimi-status", ["--json", "kimi", "status"], {"exit": 0}),
    ("kimi-claim", ["--json", "kimi", "claim"], {"exit": 0}),
    ("kimi-claim-refused", ["--json", "kimi", "claim"], {"routes": {"rpc:claim_kimi_plan_code": (400, {"error": "not_eligible"})}, "exit": 1}),
    ("credits-list", ["--json", "credits", "list"], {"exit": 0}),
    ("credits-claim", ["--json", "credits", "claim", "acme"], {"exit": 0}),
    ("missing-command", ["--json"], {"exit": 2}),
    ("group-only", ["--json", "profile"], {"exit": 2}),
    ("missing-positional", ["team", "create"], {"exit": 2, "human": True}),
    ("bad-choice", ["--lang", "fr", "whoami"], {"exit": 2, "human": True}),
    ("unknown-option", ["whoami", "--nope"], {"exit": 2, "human": True}),
    ("prefix-option", ["--json", "eval", "wait", "--tim", "0"], {"routes": {"portal:list": (200, {"data": listing("running")})}, "exit": 7}),
    ("bad-int", ["eval", "list", "--limit", "x"], {"exit": 2, "human": True}),
]


@pytest.mark.parametrize("name,argv,options", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_builds_agree(gw, tmp_path, name, argv, options):
    results = {}
    for build, command in builds():
        kwargs = {k: options[k] for k in ("routes", "stdin", "files", "env", "token", "config") if k in options}
        results[build] = run_build(command, gw, tmp_path, argv, **kwargs)
    python = results["python"]
    assert python["exit"] == options["exit"], (python["stdout"], python["stderr"])
    if "--json" in argv and python["exit"] != 2 or name in ("missing-command", "group-only") or (python["stdout"].startswith("{")):
        doc = json.loads(python["stdout"])
        assert doc["ok"] is (python["exit"] == 0)
    if "rust" not in results:
        pytest.skip("Rust build not available (set SURVEY26_RUST_BIN or build cli-rs)")
    rust = results["rust"]
    assert rust["exit"] == python["exit"], (rust["stdout"], rust["stderr"])
    if not options.get("human"):
        assert rust["stdout"] == python["stdout"]
    assert rust["requests"] == python["requests"]
    assert rust["puts"] == python["puts"]
    assert rust["files"] == python["files"]
    assert rust["config"] == python["config"]


def test_spec_matches_the_python_parser():
    assert json.loads((ROOT / "cli" / "spec.json").read_text("utf-8")) == cli_spec_introspect.spec(survey26)


def test_model_presets_match_the_website():
    web = json.loads((ROOT / "web" / "src" / "lib" / "modelProviders.json").read_text("utf-8"))
    assert survey26.MODEL_PROVIDERS == [{"cli": p["cli"], "label": p["label"], "protocol": p["protocol"],
                                         "base_url": p["baseUrl"], "model": p["model"]} for p in web]


def test_messages_match_the_python_build():
    shared = json.loads((ROOT / "cli" / "messages.json").read_text("utf-8"))
    assert shared == {k: {"en": v[0], "zh": v[1]} for k, v in survey26.MESSAGES.items()}


def test_versions_agree():
    cargo = (ROOT / "cli-rs" / "Cargo.toml").read_text()
    pyproject = (ROOT / "cli" / "pyproject.toml").read_text()
    assert f'version = "{survey26.__version__}"' in cargo and f'version = "{survey26.__version__}"' in pyproject
    if RUST.exists():
        out = subprocess.run([str(RUST), "--version"], capture_output=True, text=True).stdout.strip()
        assert out == "survey26 " + survey26.__version__


def test_every_spec_command_has_help_in_both_builds():
    spec = json.loads((ROOT / "cli" / "spec.json").read_text("utf-8"))
    for build, command in builds():
        for entry in spec["commands"]:
            done = subprocess.run(command + entry["path"] + ["--help"], capture_output=True, text=True)
            assert done.returncode == 0, (build, entry["path"], done.stderr)
            for option in entry["options"]:
                assert option["flags"][-1] in done.stdout, (build, entry["path"], option["flags"])
