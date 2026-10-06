"""survey26 command-line tool against a local stand-in of the survey26-cli gateway."""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))
import survey26  # noqa: E402

TOKEN = "s26_" + "ab" * 32
USER = "11111111-1111-1111-1111-111111111111"
PHASE = "22222222-2222-2222-2222-222222222222"
REV = "33333333-3333-3333-3333-333333333333"
REV2 = "33339999-3333-3333-3333-333333333333"
BATCH = "44444444-4444-4444-4444-444444444444"
RUN_A, RUN_B = "55555555-5555-5555-5555-555555555555", "66666666-6666-6666-6666-666666666666"
SC_A, SC_B = "77777777-7777-7777-7777-777777777777", "88888888-8888-8888-8888-888888888888"


def me(team=True):
    return {"id": USER, "email": "u@example.test", "name": "Una", "nickname": "Vega", "avatar_url": None,
            "team": {"id": "t1", "name": "Stars", "leader_id": USER, "invite_code": "ABCD1234", "max_size": 3,
                     "member_count": 1, "is_locked": False} if team else None}


def listing(batch_status="scored", evaluated=False):
    return {
        "phases": [{"phase_id": PHASE, "projects_enabled": True, "daily_batches": 40,
                    "phases": {"slug": "practice-projects", "name_en": "P", "name_zh": "P", "is_active": True,
                               "starts_at": "2026-09-25 15:17:29.196961+00", "ends_at": None}}],
        "projects": [{"id": "p", "title": "Agent", "created_at": "2026-10-01T00:00:00Z", "observer_revisions": [
            {"id": REV, "status": "reviewable", "approval_digest": "d" * 64, "source_kind": "zip", "source_location": "",
             "manifest": {"command": ["python", "agent.py"]}, "adapter_files": {}, "explanation": "", "error": "",
             "public_test": {}, "created_at": "2026-10-01T00:00:00Z"},
            {"id": REV2, "status": "approved", "approval_digest": "e" * 64, "source_kind": "zip", "source_location": "",
             "manifest": {}, "adapter_files": {}, "explanation": "", "error": "", "public_test": {},
             "created_at": "2026-10-01T00:00:00Z"}]}],
        "batches": [{"id": BATCH, "status": batch_status, "score": 61.5 if batch_status == "scored" else None,
                     "phase_id": PHASE, "revision_id": REV2 if evaluated else "other", "created_at": "2026-10-02T00:00:00Z",
                     "observer_runs": [
                         {"id": RUN_B, "scenario_id": SC_B, "status": "scored", "score": 60, "result_path": "x"},
                         {"id": RUN_A, "scenario_id": SC_A, "status": "scored", "score": 63, "result_path": "y"}]}],
        "quota": [{"phase_id": PHASE, "daily_batches": 40, "used": 1, "remaining": 39}],
        "final_versions": [{"phase_id": PHASE, "revision_id": REV2, "source": "best", "deadline": None, "locked": False}],
    }


def result_zip(wrapper: str, files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(wrapper + name, content)
    return buf.getvalue()


class Gateway:
    """Records every request; `routes` maps an operation key to (status, payload)."""

    def __init__(self):
        self.requests: list = []
        self.routes: dict = {}
        self.files: dict = {}
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                content = gateway.files.get(self.path)
                self.send_response(200 if content is not None else 404)
                self.end_headers()
                self.wfile.write(content or b"")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                gateway.requests.append({"auth": self.headers.get("authorization"), "body": body})
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

    def ops(self):
        return [r["body"]["op"] + ":" + (r["body"].get("name") or (r["body"].get("fields") or {}).get("action") or "")
                for r in self.requests]


@pytest.fixture
def gw(monkeypatch, tmp_path):
    g = Gateway()
    monkeypatch.setenv("SURVEY26_API", g.url)
    monkeypatch.setenv("SURVEY26_TOKEN", TOKEN)
    monkeypatch.setenv("SURVEY26_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("SURVEY26_LANG", "en")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(survey26.time, "sleep", lambda s: None)
    monkeypatch.chdir(tmp_path)
    g.routes["whoami"] = (200, {"data": {"me": me(), "token_id": "t"}})
    g.routes["portal:list"] = (200, {"data": listing()})
    g.routes["rpc:current_competition"] = (200, {"data": {"mode": "practice", "phase_id": "x", "project_phase_id": PHASE}})
    g.routes["scenarios"] = (200, {"data": [{"id": SC_A, "slug": "v4-practice-alpha", "name": "a"},
                                            {"id": SC_B, "slug": "v4-practice-beta", "name": "b"}]})
    yield g
    g.server.shutdown()


def run(capsys, *argv):
    code = survey26.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(capsys, *argv):
    code, out, _ = run(capsys, "--json", *argv)
    return code, json.loads(out)


def test_no_token_and_malformed_token_fail_with_auth_exit_and_no_request(gw, capsys, monkeypatch):
    monkeypatch.delenv("SURVEY26_TOKEN")
    code, doc = run_json(capsys, "whoami")
    assert code == 3 and doc == {"ok": False, "command": "whoami", "error": {
        "code": "no_token", "message": survey26.MESSAGES["no_token"][0], "exit_code": 3}}
    code, doc = run_json(capsys, "--token", "eyJ.not.a.token", "whoami")
    assert code == 3 and doc["error"]["code"] == "invalid_token"
    assert gw.requests == []


def test_whoami_json_schema_and_bearer_token(gw, capsys):
    code, doc = run_json(capsys, "whoami")
    assert code == 0 and doc["ok"] is True and doc["command"] == "whoami"
    assert doc["data"]["team"]["is_captain"] is True and doc["data"]["nickname"] == "Vega"
    assert gw.requests[0]["auth"] == "Bearer " + TOKEN


@pytest.mark.parametrize("status,error,exit_code", [
    (401, "invalid_token", 3), (401, "cli_tokens_disabled", 3), (429, "rate_limited", 5), (400, "daily_limit", 8),
    (400, "batch_already_active", 8), (404, "run_not_found", 4), (403, "action_not_available", 1),
    (400, "full", 8), (400, "some_new_code", 1)])
def test_server_errors_map_to_exit_codes_and_site_messages(gw, capsys, status, error, exit_code):
    gw.routes["whoami"] = (status, {"error": error})
    code, doc = run_json(capsys, "whoami")
    assert code == exit_code and doc["error"]["code"] == error
    assert doc["error"]["message"] == survey26.MESSAGES.get(error, (error,))[0]


def test_messages_follow_language(gw, capsys, monkeypatch):
    gw.routes["whoami"] = (400, {"error": "daily_limit"})
    monkeypatch.setenv("SURVEY26_LANG", "zh")
    code, _, err = run(capsys, "whoami")
    assert code == 8 and "今天的评测次数已用完" in err


def test_unavailable_gateway_is_retried_for_reads_only(gw, capsys):
    gw.routes["whoami"] = (503, {"error": "gateway_unavailable"})
    code, doc = run_json(capsys, "whoami")
    assert code == 6 and len(gw.requests) == 3
    gw.requests.clear()
    gw.routes["rpc:join_team"] = (503, {"error": "gateway_unavailable"})
    code, doc = run_json(capsys, "team", "join", "abcd1234")
    assert code == 6 and gw.ops() == ["rpc:join_team"]
    assert gw.requests[0]["body"]["args"] == {"p_invite_code": "ABCD1234"}


def test_confirmations_never_prompt_without_terminal(gw, capsys):
    gw.routes["rpc:leave_team"] = (200, {"data": None})
    code, doc = run_json(capsys, "team", "leave")
    assert code == 2 and doc["error"]["code"] == "confirmation_required" and "rpc:leave_team" not in gw.ops()
    code, doc = run_json(capsys, "team", "leave", "--yes")
    assert code == 0 and gw.ops()[-1] == "rpc:leave_team"


def test_evaluate_again_needs_yes_and_sends_confirm_repeat(gw, capsys):
    gw.routes["portal:list"] = (200, {"data": listing(evaluated=True)})
    gw.routes["portal:evaluate"] = (200, {"data": {"batch_id": BATCH}})
    code, doc = run_json(capsys, "eval", "start", "33339")
    assert code == 2 and "portal:evaluate" not in gw.ops()
    code, doc = run_json(capsys, "eval", "start", "33339", "--yes")
    assert code == 0 and doc["data"] == {"batch_id": BATCH, "phase_id": PHASE, "phase": "practice-projects",
                                         "revision_id": REV2, "repeat": True, "model_disabled": False}
    sent = [r["body"]["fields"] for r in gw.requests if r["body"]["op"] == "portal"][-1]
    assert sent == {"action": "evaluate", "phase_id": PHASE, "revision_id": REV2, "confirm_repeat": True}


def test_first_evaluation_and_selfcheck(gw, capsys):
    gw.routes["portal:evaluate"] = (200, {"data": {"batch_id": BATCH}})
    gw.routes["rpc:observer_create_repeat_batches"] = (200, {"data": ["a", "b", "c"]})
    code, doc = run_json(capsys, "eval", "start", REV2)
    assert code == 0 and doc["data"]["repeat"] is False
    assert "confirm_repeat" not in [r["body"]["fields"] for r in gw.requests if r["body"]["op"] == "portal"][-1]
    code, doc = run_json(capsys, "eval", "selfcheck", REV2, "--yes")
    assert code == 0
    assert gw.requests[-1]["body"]["args"] == {"p_phase": PHASE, "p_revision": REV2, "p_confirm_repeat": True}


def test_id_prefixes_must_be_unique(gw, capsys):
    code, doc = run_json(capsys, "project", "show", "3333")
    assert code == 2 and doc["error"]["code"] == "ambiguous_id"
    code, doc = run_json(capsys, "project", "show", "33333333")
    assert code == 0 and doc["data"]["revision_id"] == REV and doc["data"]["approval_digest"] == "d" * 64
    code, doc = run_json(capsys, "project", "show", "abcdef")
    assert code == 4


def test_confirm_sends_the_reviewed_digest(gw, capsys):
    gw.routes["portal:approve"] = (200, {"data": {"accepted": True}})
    code, doc = run_json(capsys, "project", "confirm", "33333333", "--yes")
    assert code == 0
    assert gw.requests[-1]["body"]["fields"] == {"action": "approve", "revision_id": REV, "digest": "d" * 64}
    code, doc = run_json(capsys, "project", "confirm", "33339999", "--yes")
    assert code == 1 and doc["error"]["code"] == "revision_not_reviewable"


def test_eval_wait_outcomes(gw, capsys, monkeypatch):
    code, doc = run_json(capsys, "eval", "wait")
    assert code == 0 and doc["data"]["score"] == 61.5
    assert [r["label"] for r in doc["data"]["runs"]] == ["Practice card α", "Practice card β"]
    gw.routes["portal:list"] = (200, {"data": listing("failed")})
    code, doc = run_json(capsys, "eval", "wait", "44444444")
    assert code == 9 and doc["error"]["code"] == "evaluation_failed"
    gw.routes["portal:list"] = (200, {"data": listing("running")})
    clock = iter(range(0, 10000, 100))
    monkeypatch.setattr(survey26.time, "time", lambda: next(clock))
    code, doc = run_json(capsys, "eval", "wait", "--timeout", "250")
    assert code == 7 and doc["error"]["code"] == "wait_timeout"


def test_download_all_orders_cards_flattens_and_reports_failures(gw, capsys, tmp_path):
    gw.files["/a.zip"] = result_zip("runner-abc123/", {"result.json": "{}", "decisions.csv": "x"})
    gw.routes["portal:download_result"] = (200, lambda body: (200, {"data": {"url": gw.url + "/a.zip"}})
                                           if body["fields"]["run_id"] == RUN_A else (404, {"error": "result_not_ready"}))
    code, doc = run_json(capsys, "results", "download-all", "latest", "-o", "all.zip")
    assert code == 0 and doc["data"]["errors"] == ["2-practice-beta: result_not_ready"]
    with zipfile.ZipFile(tmp_path / "all.zip") as z:
        assert sorted(z.namelist()) == ["1-practice-alpha/decisions.csv", "1-practice-alpha/result.json", "errors.txt",
                                        "evaluation.json"]
        meta = json.loads(z.read("evaluation.json"))
        assert meta["model_provided"] is True and meta["model_disabled"] is False and meta["evaluation_id"] == BATCH


def test_single_result_and_agent_log(gw, capsys, tmp_path):
    gw.files["/a.zip"] = b"PK-result"
    gw.routes["portal:download_result"] = (200, {"data": {"url": gw.url + "/a.zip"}})
    gw.routes["portal:diagnostics"] = (200, {"data": [{"kind": "execute", "status": "ok", "code": "", "log": "fine"}]})
    gw.routes["portal:agent_log"] = (200, {"data": {"available": True, "bytes": 12, "truncated": False, "log": "a\nb\nc\n"}})
    code, doc = run_json(capsys, "results", "download", "55555555")
    assert code == 0 and Path(doc["data"]["path"]).read_bytes() == b"PK-result" and doc["data"]["card"] == "v4-practice-alpha"
    code, doc = run_json(capsys, "results", "log", "55555555", "--tail", "2")
    assert code == 0 and doc["data"]["log"] == "b\nc" and doc["data"]["diagnostics"][0]["log"] == "fine"
    code, doc = run_json(capsys, "results", "log", "55555555", "-o", "agent.log")
    assert code == 0 and (tmp_path / "agent.log").read_text() == "a\nb\nc\n"
    assert gw.requests[-1]["body"]["fields"]["full"] is True


def test_secret_values_go_to_the_server_but_never_to_output(gw, capsys, monkeypatch):
    env = {"enabled": True, "domains": [], "variables": [
        {"name": "KIMI_API_KEY", "secret": True, "hint": "wxyz", "value": None, "updated_at": "t"}]}
    gw.routes["portal:save_team_variable"] = (200, {"data": {"team_environment": env}})
    monkeypatch.setattr(sys, "stdin", io.StringIO("sk-very-secret-wxyz\n"))
    code, out, err = run(capsys, "--json", "env", "set", "KIMI_API_KEY", "--value-stdin")
    assert code == 0 and "sk-very-secret" not in out + err
    assert json.loads(out)["data"]["variables"][0]["masked_value"] == "****wxyz"
    assert gw.requests[-1]["body"]["fields"] == {"action": "save_team_variable", "name": "KIMI_API_KEY",
                                                 "value": "sk-very-secret-wxyz", "secret": True}
    code, doc = run_json(capsys, "env", "set", "X")
    assert code == 2


def test_upload_reserves_puts_and_submits(gw, capsys, tmp_path, monkeypatch):
    project = tmp_path / "agent.zip"
    with zipfile.ZipFile(project, "w") as z:
        z.writestr("agent.py", "print(1)")
    puts = []
    monkeypatch.setattr(survey26, "http_put", lambda url, data, headers, timeout=600: puts.append((url, headers)))
    gw.routes["portal:upload"] = (200, {"data": {"id": "u1", "path": "p/source.zip", "token": "t",
                                                 "upload_url": "https://storage.example/sign?token=t", "apikey": "anon"}})
    gw.routes["portal:submit_zip"] = (200, {"data": {"revision_id": REV}})
    code, doc = run_json(capsys, "project", "upload", str(project), "--title", "Agent v2")
    assert code == 0 and doc["data"]["revision_id"] == REV and puts[0][0] == "https://storage.example/sign?token=t"
    assert gw.requests[-1]["body"]["fields"] == {"action": "submit_zip", "title": "Agent v2", "upload_id": "u1"}
    (tmp_path / "notzip.zip").write_text("hello")
    code, doc = run_json(capsys, "project", "upload", str(tmp_path / "notzip.zip"))
    assert code == 1 and doc["error"]["code"] == "wrong_file_type"


def test_final_version_and_profile_and_leaderboard(gw, capsys):
    gw.routes["portal:set_final_version"] = (200, {"data": {"final_version": {"revision_id": REV2, "source": "chosen"}}})
    code, doc = run_json(capsys, "final", "set", "33339999")
    assert code == 0 and gw.requests[-1]["body"]["fields"] == {"action": "set_final_version", "phase_id": PHASE, "revision_id": REV2}
    code, doc = run_json(capsys, "final", "clear", "--yes")
    assert gw.requests[-1]["body"]["fields"]["revision_id"] is None
    gw.routes["profile_update"] = (200, {"data": {"me": me()}})
    code, doc = run_json(capsys, "profile", "set", "--nickname", "Vega", "--github", "@vega")
    assert code == 0 and gw.requests[-1]["body"]["fields"] == {"nickname": "Vega", "github": "vega"}
    code, doc = run_json(capsys, "profile", "set", "--nickname", "x" * 41)
    assert code == 1 and doc["error"]["code"] == "nickname_too_long"
    gw.routes["phases"] = (200, {"data": [{"id": PHASE, "slug": "online", "observer_settings": {"projects_enabled": True}}]})
    gw.routes["rpc:observer_card_board"] = (200, {"data": {"layout": "cards_overall", "cards": [], "scenario": None, "rows": [
        {"rank": 1, "team_id": "other", "team_name": "A", "total_score": 70}, {"rank": 2, "team_id": "t1", "team_name": "Stars", "total_score": 61}]}})
    code, doc = run_json(capsys, "leaderboard", "--phase", "online", "--mine")
    assert code == 0 and [r["team_name"] for r in doc["data"]["rows"]] == ["Stars"]


def test_login_saves_token_privately(gw, capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("SURVEY26_TOKEN")
    monkeypatch.setattr(sys, "stdin", io.StringIO(TOKEN + "\n"))
    code, doc = run_json(capsys, "login", "--token-stdin")
    assert code == 0
    path = tmp_path / "config.json"
    assert json.loads(path.read_text())["token"] == TOKEN and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    code, doc = run_json(capsys, "whoami")
    assert code == 0 and gw.requests[-1]["auth"] == "Bearer " + TOKEN
    code, doc = run_json(capsys, "logout")
    assert code == 0 and "token" not in json.loads(path.read_text())


def test_every_command_has_help_and_usage_errors_exit_2(capsys):
    parser = survey26.build_parser()
    assert "Exit codes" in parser.format_help()
    with pytest.raises(SystemExit) as raised:
        survey26.main(["team", "create"])
    assert raised.value.code == 2


def test_messages_match_the_website_wording():
    site = {lang: json.loads((ROOT / "web" / "src" / "i18n" / f"{lang}.json").read_text("utf-8")) for lang in ("en", "zh")}
    shared = ["already_in_team", "bad_code", "full", "locked", "name_length", "name_taken", "bad_size", "leader_only",
              "leader_must_transfer", "has_submissions", "not_in_team", "not_member", "cannot_remove_leader", "need_team",
              "team_limit_reached", "no_codes_left", "already_assigned"]
    for index, lang in enumerate(("en", "zh")):
        for key in shared:
            assert survey26.MESSAGES[key][index] == site[lang]["errors"][key], key
        for key in ("recipient_unavailable", "team_unavailable", "invitation_not_found", "invitation_finished"):
            assert survey26.MESSAGES[key][index] == site[lang]["team"]["errors"][key], key
        env = site[lang]["submit"]["team_env"]
        for code, key in (("invalid_team_variable", "invalid_variable"), ("team_variable_limit", "variable_limit"),
                          ("invalid_team_domains", "invalid_domain"), ("team_domain_not_public", "domain_not_public")):
            assert survey26.MESSAGES[code][index] == env[key], code
        for key in ("captain_only", "not_eligible"):
            assert survey26.MESSAGES[key][index] == site[lang]["kimi_plan"]["errors"][key], key


def test_agents_guide_is_generated_from_the_site_guide():
    sys.path.insert(0, str(ROOT / "cli"))
    import build_agents_md
    assert (ROOT / "cli" / "AGENTS.md").read_text("utf-8") == build_agents_md.render()


def test_waits_pause_on_rate_limit_instead_of_failing(gw, capsys):
    answers = iter([(429, {"error": "rate_limited"}), (503, {"error": "gateway_unavailable"})] * 1)
    gw.routes["portal:list"] = (200, lambda body: next(answers, (200, {"data": listing()})))
    code, doc = run_json(capsys, "eval", "wait", "latest")
    assert code == 0 and doc["data"]["status"] == "scored"


def test_cut_short_answers_are_retried_for_reads_and_reported_for_writes(gw, capsys, monkeypatch):
    import http.client
    real = survey26.urllib.request.urlopen
    failures = {"left": 1}

    def flaky(request, timeout=None):
        if failures["left"]:
            failures["left"] -= 1
            raise http.client.IncompleteRead(b"")
        return real(request, timeout=timeout)
    monkeypatch.setattr(survey26.urllib.request, "urlopen", flaky)
    code, doc = run_json(capsys, "whoami")
    assert code == 0
    failures["left"] = 1
    gw.routes["rpc:join_team"] = (200, {"data": None})
    code, doc = run_json(capsys, "team", "join", "ABCD1234")
    assert code == 6 and doc["error"]["code"] == "network_error"


ONLINE = "44444444-4444-4444-4444-444444444444"


@pytest.mark.parametrize("online_ends,comp_extra,flag,expected", [
    # During the online competition the default is online; practice stays reachable with --phase.
    ("2099-01-01T00:00:00Z", {"practice_phase_id": PHASE}, [], "online"),
    ("2099-01-01T00:00:00Z", {"practice_phase_id": PHASE}, ["--phase", "practice-projects"], "practice-projects"),
    # After the online deadline the server also answers project_phase_id, so every CLI defaults to practice.
    ("2020-01-01T00:00:00Z", {"practice_phase_id": PHASE, "project_phase_id": PHASE}, [], "practice-projects"),
])
def test_default_phase_during_and_after_the_online_competition(gw, capsys, online_ends, comp_extra, flag, expected):
    data = listing()
    data["phases"].append({"phase_id": ONLINE, "projects_enabled": True, "daily_batches": 40,
                           "phases": {"slug": "online", "name_en": "Online", "name_zh": "线上赛", "is_active": True,
                                      "starts_at": "2026-10-04 16:00:00+00", "ends_at": online_ends}})
    data["quota"].append({"phase_id": ONLINE, "daily_batches": 40, "used": 0, "remaining": 40})
    gw.routes["portal:list"] = (200, {"data": data})
    gw.routes["rpc:current_competition"] = (200, {"data": {"mode": "competition", "phase_id": ONLINE, **comp_extra}})
    gw.routes["rpc:my_observer_phase"] = (200, {"data": None})
    gw.routes["portal:evaluate"] = (200, {"data": {"batch_id": BATCH}})
    code, doc = run_json(capsys, "eval", "start", REV2, "--yes", *flag)
    assert code == 0 and doc["data"]["phase"] == expected
