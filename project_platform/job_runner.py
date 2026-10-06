"""Finite trusted workflow entrypoint, never a participant-selected host command."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .artifacts import download_project, pack_results, store_private_artifact, upload_artifact
from .docker_runtime import DockerWorkspace
from .diagnostics import ProjectJobFailure, agent_log, private_log, safe_code
from .egress import RestrictedEgress, anthropic_base
from .team_egress import TeamEgress, checked_team_egress, report_text
from .executor import execute
from .job_client import GitHubIdentity, Http, JobClient, JobError, retrying
from .manifest import ProjectError, ProjectManifest
from .package import MAX_ARCHIVE_BYTES, extract_project, project_digest, read_project_zip
from .sealing import OVERHEAD, SealKey, decode_key, seal
from .preparation import prepare_project
from .session import SessionClient
from .scenario_job import prepare_bounded
from .scenario_instances import InstanceError
from .trusted_engine import ColocatedProvider, result_summary, run_session
from challenge import v4_workflow
from challenge.scoring_core import score_files
from challenge.v4_workflow import is_v4_bundle

# Carried from the execute handler to run_claimed only; never part of a receipt.
AGENT_LOG_KEY = "_agent_log"


def deliver_agent_log(client: JobClient, text: str | None) -> None:
    """Best effort: a missing log must never change the run's outcome."""
    if not text:
        return
    try:
        client.agent_log(text)
    except Exception:
        pass


def _team(payload: dict) -> dict | None:
    """The team's variables and allowed domains, when the platform sends them.

    With them the project reaches only those domains (team egress) and gets the
    variables as its environment; the platform model proxy is not involved."""
    if "team_egress" not in payload:
        return None
    return checked_team_egress(payload["team_egress"])


def _team_environment(environment: dict[str, str]) -> dict[str, str]:
    """Run identity only: no model-proxy settings next to the team's own variables."""
    return {key: environment[key] for key in ("OBSERVER_API_URL", "OBSERVER_RUN_TOKEN", "OBSERVER_RUN_ID",
                                              "OBSERVER_MODEL_DISABLED") if key in environment}


_PROXY_MODEL_ENV = ("OPENAI_BASE_URL", "OPENAI_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY")


def _without_model(payload: dict, environment: dict[str, str]) -> dict[str, str]:
    """An evaluation without a model (本次不提供模型): the platform has already left out the team's
    model variables; the project also gets no model-proxy settings and OBSERVER_MODEL_DISABLED=1."""
    if payload.get("model_disabled") is not True:
        return environment
    return {**{k: v for k, v in environment.items() if k not in _PROXY_MODEL_ENV}, "OBSERVER_MODEL_DISABLED": "1"}


def _team_egress(team: dict, client_env: dict[str, str]) -> TeamEgress:
    """Open egress (any public destination) or the team's allow-list, as the payload says."""
    return TeamEgress(team["domains"], client_env=client_env, open=team.get("open") is True,
                      **({"route": team["route"]} if team.get("route") is not None else {}))


def _egress_text(egress) -> str:
    return report_text(egress.report, getattr(egress, "route_summary", None))


def _egress_receipt(target: dict, egress) -> None:
    """The per-destination record and, with a route, its summary (labels and counters only)."""
    target["egress"] = egress.report
    if getattr(egress, "route_summary", None) is not None:
        target["egress_route"] = egress.route_summary


def _start_with(runtime, egress: TeamEgress, team: dict, environment: dict[str, str]):
    return runtime.start({**_team_environment(environment), **egress.environment}, team=team,
                         hosts=tuple(egress.hosts), dns=egress.dns)


def _team_secrets(team: dict | None) -> tuple[str, ...]:
    if team is None:
        return ()
    return tuple(team["environment"][name] for name in team["secrets"] if len(team["environment"][name]) >= 4)


def execute_job(payload: dict, root: Path, http: Http) -> dict:
    files = download_project(http, payload["archive_url"])
    # This is the digest of the fully materialized, approved project, including
    # adapter and reviewed manifest. It is not the original pre-adaptation hash.
    if project_digest(files) != payload["source_digest"]:
        raise JobError("project_digest_mismatch")
    manifest = ProjectManifest.parse(payload["manifest"])
    workspace = root / "project"
    extract_project(files, workspace)
    client = SessionClient(payload["session_url"], payload["run_credential"])
    environment = {
        "OBSERVER_API_URL": payload["session_url"], "OBSERVER_RUN_TOKEN": payload["run_credential"],
        "OBSERVER_RUN_ID": payload["run_id"],
        "OPENAI_BASE_URL": payload["model_base_url"], "OPENAI_API_KEY": payload["run_credential"],
        "ANTHROPIC_BASE_URL": anthropic_base(payload["model_base_url"]), "ANTHROPIC_API_KEY": payload["run_credential"],
    }
    environment = _without_model(payload, environment)
    runtime= DockerWorkspace(workspace, manifest, manifest.image)
    team = _team(payload)
    secrets = (payload['run_credential'], *_team_secrets(team))
    egress = None
    def logs():
        stderr = runtime.transport.log if runtime.transport else ''
        return (private_log(runtime.build_log+'\n'+stderr, secrets),
                agent_log(runtime.build_log, stderr, secrets,
                          truncated=bool(runtime.transport and runtime.transport.log_truncated)))
    try:
        # The image is immutable; pull happens on the disposable execution host,
        # before any participant process. No installation/model master keys exist.
        runtime.pull()
        egress = _team_egress(team, runtime.client_env) if team is not None else None
        if egress is not None:
            # The build keeps its registry access; only the run joins the internal network.
            runtime.build()
            runtime.build = lambda: runtime.build_log
            egress.start()
            runtime.network = egress.network
            start = runtime.start
            runtime.start = lambda env: start({**_team_environment(env), **egress.environment}, team=team,
                                              hosts=tuple(egress.hosts), dns=egress.dns)
        outcome = execute(runtime, client, environment)
    except Exception as error:
        runtime.close()
        if egress is not None:
            egress.close()
        log, full = logs()
        failure = ProjectJobFailure({'stage':'execute','code':safe_code(error),'log':log})
        failure.agent_log = (full or '') + (_egress_text(egress) if egress is not None else '')
        if egress is not None:
            failure.egress = egress.report
            failure.egress_route = getattr(egress, "route_summary", None)
        raise failure from None
    finally:
        runtime.close()
        if egress is not None:
            egress.close()
    log, full = logs()
    result = {"run_id": payload["run_id"], "status": outcome["status"],
              'diagnostics':{'stage':'execute','code':'completed','log':log},
              AGENT_LOG_KEY: (full or '') + (_egress_text(egress) if egress is not None else '')}
    if egress is not None:
        _egress_receipt(result, egress)
    return result


class SealedTransfer:
    """Public runner pool: inputs arrive sealed to this job's in-memory key and
    the result leaves sealed to the backend's public key (project_platform.sealing).
    Signed URLs only ever see ciphertext, and this runner never receives a
    repository token: the backend commits the opened result itself."""

    def __init__(self, client: JobClient):
        if client.seal_key is None:
            raise JobError("sealed_transfer_unavailable")
        self.client = client

    def download(self, http: Http, url: str, name: str, digest: str | None = None):
        raw = retrying(lambda: http.request(url, limit=MAX_ARCHIVE_BYTES + OVERHEAD, timeout=120))
        data = self.client.seal_key.open(raw, name + ":" + self.client.job_id)
        if digest is not None and hashlib.sha256(data).hexdigest() != digest:
            raise JobError("archive_digest_mismatch")
        return read_project_zip(data)

    def publish(self, http: Http, payload: dict, archive: bytes) -> str:
        if len(archive) + OVERHEAD > MAX_ARCHIVE_BYTES:
            raise JobError("artifact_too_large")
        sealed = seal(decode_key(payload.get("result_key")), archive, "result:" + self.client.job_id)

        def put():
            # A fresh signed URL per attempt; the object is upserted, so a
            # repeated PUT of the same ciphertext is harmless.
            upload = self.client.result_upload()
            if upload["path"] != payload["artifact_upload"].get("path"):
                raise JobError("invalid_artifact_destination")
            http.request(upload["url"], data=sealed, method="PUT", headers={"Content-Type": "application/zip"},
                         limit=65536)

        retrying(put)
        return self.client.store_sealed_result()


def _download(http: Http, url: str, digest: str | None, sealed: SealedTransfer | None, name: str):
    if sealed is None:
        return download_project(http, url, digest)
    return sealed.download(http, url, name, digest)


def _participant_runtime(payload: dict, participant: dict, root: Path, http: Http, sealed=None):
    files = _download(http, payload["archive_url"], None, sealed, "project")
    if project_digest(files) != participant["source_digest"]:
        raise JobError("project_digest_mismatch")
    manifest = ProjectManifest.parse(participant["manifest"])
    workspace = root / "project"
    extract_project(files, workspace)
    environment = {
        "OBSERVER_API_URL": payload["session_url"], "OBSERVER_RUN_TOKEN": participant["run_credential"],
        "OBSERVER_RUN_ID": payload["run_id"],
        "OPENAI_BASE_URL": participant["model_base_url"], "OPENAI_API_KEY": participant["run_credential"],
        "ANTHROPIC_BASE_URL": anthropic_base(participant["model_base_url"]),
        "ANTHROPIC_API_KEY": participant["run_credential"],
    }
    return DockerWorkspace(workspace, manifest, manifest.image), environment


def engine_job(payload: dict, root: Path, http: Http, *, repository_credentials=None, sealed=None) -> dict:
    if ((payload.get("artifact_upload") or {}).get("kind") == "sealed") != (sealed is not None):
        raise JobError("sealed_transfer_mismatch")
    files = _download(http, payload["scenario_url"], payload["scenario_digest"], sealed, "scenario")
    scenario, output = root / "scenario", root / "result"
    extract_project(files, scenario)
    client = SessionClient(payload["session_url"], payload["run_credential"])
    participant = payload.get("colocated")
    if is_v4_bundle(scenario) and (participant is None or payload.get("instance") is not None):
        # v4 cards run only next to the participant container (thousands of steps).
        raise JobError("v4_requires_colocated")
    if participant is not None:
        # Public scenarios only: the scheduler never colocates a private instance.
        if payload.get("instance") is not None:
            raise JobError("colocated_private_instance")
        runtime, environment = _participant_runtime(payload, participant, root, http, sealed)
        environment = _without_model(payload, environment)
        team = _team(payload)
        secrets = (payload["run_credential"], participant["run_credential"], *_team_secrets(team))
        # Team egress: the team's variables and allowed domains, nothing else.
        # Otherwise the organizer switch (observer_hardening.restricted_egress),
        # carried in the payload: the running project reaches the model proxy only.
        if team is not None:
            egress = _team_egress(team, runtime.client_env)
        elif payload.get("restricted_egress") is True:
            egress = RestrictedEgress(participant["model_base_url"], client_env=runtime.client_env, local=http.local)
        else:
            egress = None
        try:
            # The trusted forwarder image downloads while the project builds; a
            # failed build does not wait for it.
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                forwarder = pool.submit(egress.pull) if egress else None
                runtime.pull()
                runtime.build()
                if forwarder:
                    forwarder.result()
            finally:
                pool.shutdown(wait=False)
            if isinstance(egress, TeamEgress):
                egress.start()
                runtime.network = egress.network
                transport = _start_with(runtime, egress, team, environment)
            else:
                if egress:
                    egress.start()
                    runtime.network = egress.network
                    if "OPENAI_API_KEY" in environment:
                        environment = {**environment, "OPENAI_BASE_URL": egress.base_url,
                                       "ANTHROPIC_BASE_URL": egress.anthropic_base_url}
                transport = runtime.start(environment)
            provider = ColocatedProvider(transport, client, SessionClient(payload["session_url"], participant["run_credential"]))
            result, digest = run_session(scenario, output, client, wallclock_seconds=payload["runtime_seconds"],
                                        provider=provider)
        except Exception as error:
            failure = ProjectJobFailure({'stage':'execute','code':safe_code(error),
                'log':private_log(runtime.build_log+'\n'+(runtime.transport.log if runtime.transport else ''),secrets)})
            runtime.close()
            if isinstance(egress, TeamEgress):
                egress.close()
                failure.egress = egress.report
                failure.egress_route = getattr(egress, "route_summary", None)
            raise failure from None
        finally:
            runtime.close()
            if egress:
                egress.close()
        # No separate execute job here: the team's own log goes straight into
        # its private result, next to decisions.csv.
        text = agent_log(runtime.build_log, runtime.transport.log if runtime.transport else '', secrets,
                         truncated=bool(runtime.transport and runtime.transport.log_truncated))
        if isinstance(egress, TeamEgress):
            # The platform's record of every destination (no content), for the team and the organizers.
            text = (text or '') + _egress_text(egress)
            (output / "egress.json").write_text(json.dumps(
                egress.report if getattr(egress, "route_summary", None) is None else
                {"destinations": egress.report, "route": egress.route_summary}, indent=1))
        if text:
            (output / "agent.log").write_text(text)
        receipt = _publish_result(payload, client, output, result, digest, http, repository_credentials, sealed)
        if isinstance(egress, TeamEgress):
            _egress_receipt(receipt, egress)
        return receipt
    if sealed is not None:
        # The public pool takes colocated public-scenario runs only.
        raise JobError("sealed_transfer_mismatch")
    record = None
    if payload.get("instance") is not None:
        instance = payload["instance"]
        if instance["bundle_digest"] != payload["scenario_digest"]:
            raise JobError("calibration_template_mismatch")
        try:
            scenario, record = prepare_bounded(scenario, root / "instance", seed=instance["seed"],
                profile=instance["profile"], max_candidates=instance["max_candidates"])
        except InstanceError as error:
            code = str(error)
            if code not in {"scenario_preparation_timeout", "scenario_preparation_failed", "no_comparable_scenario"}:
                code = "scenario_preparation_failed"
            raise JobError(code) from None
        # This acknowledgement is required before the first participant-visible
        # message. Private inputs never enter pack_results or the result repo.
        client.call("record_instance", record=record)
    result, digest = run_session(scenario, output, client, wallclock_seconds=payload["runtime_seconds"],
                                instance_record=record)
    return _publish_result(payload, client, output, result, digest, http, repository_credentials)


def _publish_result(payload, client, output, result, digest, http, repository_credentials, sealed=None) -> dict:
    archive = pack_results(output)
    if sealed is not None:
        path = sealed.publish(http, payload, archive)
    elif payload['artifact_upload'] == {'kind': 'github'}:
        path = store_private_artifact(read_project_zip(archive), payload['run_id'], 'results', repository_credentials)
    else:
        path = upload_artifact(http, payload["artifact_upload"], archive)
    # Upload succeeds before score publication. Execution receipts cannot call
    # this method because they do not possess the separate engine capability.
    client.call("finish", summary=result_summary(result), decisions_digest=digest, result_path=path)
    return {"run_id": payload["run_id"], "result_path": path, "decisions_digest": digest}


def _trace_rejected() -> ProjectJobFailure:
    # The stored result is not the trace the engine committed at finish (or it
    # does not replay to it): evidence of tampering, never a transient error.
    return ProjectJobFailure({'stage': 'score', 'code': 'score_trace_mismatch', 'log': ''})


def score_job(payload: dict, root: Path, http: Http, sealed: SealedTransfer | None = None) -> dict:
    """Independent rescore of a finished run; no participant code runs here.

    Downloads the scenario and the run's stored result, proves decisions.csv is
    byte-identical to the digest the engine committed at finish, and recomputes
    the score from it: v4 replays the recorded actions with the deterministic
    runner (which must regenerate the same decisions.csv), v3 scores the trace
    with challenge.scoring_core. The platform compares this with the engine's
    self-reported score and keeps the recomputed one.
    """
    # Public runner pool: the scenario and the run's result arrive sealed to this
    # job's in-memory key (ops/public-runner-pool.md); the score goes back to the
    # job API only, never to the log.
    files = _download(http, payload["scenario_url"], payload["scenario_digest"], sealed, "scenario")
    scenario = root / "scenario"
    extract_project(files, scenario)
    artifact = {item.path: item.data for item in _download(http, payload["result_url"], None, sealed, "trace")}
    trace = artifact.get("decisions.csv")
    if trace is None or hashlib.sha256(trace).hexdigest() != payload["decisions_digest"]:
        raise _trace_rejected()
    evidence, replayed = root / "evidence", root / "replayed"
    evidence.mkdir()
    (evidence / "decisions.csv").write_bytes(trace)
    if is_v4_bundle(scenario):
        actions = artifact.get(v4_workflow.ACTIONS_FILE)
        if actions is None:
            raise _trace_rejected()
        (evidence / v4_workflow.ACTIONS_FILE).write_bytes(actions)
        report = v4_workflow.replay(scenario, evidence / v4_workflow.ACTIONS_FILE, replayed)
        if hashlib.sha256((replayed / "decisions.csv").read_bytes()).hexdigest() != payload["decisions_digest"]:
            raise _trace_rejected()
        score = {"total": report["total"], **{key: report["components"][key] for key in sorted(report["components"])}}
    else:
        report = score_files(scenario, evidence / "decisions.csv", replayed / "score_report.json",
                             payload["termination_reason"])
        score = report["score"]
    return {"run_id": payload["run_id"], "decisions_digest": payload["decisions_digest"], "score": score}


def run_claimed(kind: str, client: JobClient, root: Path) -> None:
    payload = client.claim()
    if payload.get("kind") != kind or payload.get("job_id") != client.job_id:
        raise JobError("job_kind_mismatch")
    try:
        handler = {
            'execute': execute_job,
            'engine': lambda payload, root, http: engine_job(
                payload, root, http, repository_credentials=client.artifact_repository,
                sealed=SealedTransfer(client) if client.seal_key is not None else None),
            'score': lambda payload, root, http: score_job(
                payload, root, http, sealed=SealedTransfer(client) if client.seal_key is not None else None),
            'prepare': lambda payload, root, http: prepare_project(payload, http, repository_credentials=client.artifact_repository),
        }.get(kind)
        if handler is None:
            raise JobError("job_kind_unavailable")
        result = handler(payload, root, client.http)
    except Exception as error:
        # Exception strings may contain signed URLs, project output or model
        # credentials. Detailed diagnostics must use the private artifact path.
        if kind == "engine":
            try:
                SessionClient(payload["session_url"], payload["run_credential"]).call("fail", error="engine_job_failed")
            except Exception:
                pass
        if isinstance(error,ProjectJobFailure):
            diagnostics=error.diagnostics
        elif isinstance(error,ProjectError):
            # Fixed, participant-facing wording (never secrets or project output):
            # e.g. the automatic adapter could not find an entry point.
            diagnostics={'stage':kind,'code':'project_error','log':private_log(str(error)[:500])}
        else:
            diagnostics={'stage':kind,'code':safe_code(error),'log':''}
        if kind == "execute":
            # Must precede the receipt: the job API binds the log to this run
            # only while the job is still claimed.
            deliver_agent_log(client, getattr(error, "agent_log", None))
        receipt = {'diagnostics':diagnostics}
        if isinstance(getattr(error, "egress", None), list):
            receipt["egress"] = error.egress
            if isinstance(getattr(error, "egress_route", None), dict):
                receipt["egress_route"] = error.egress_route
        client.complete(receipt, error=kind + "_job_failed")
        raise JobError(kind + "_job_failed") from None
    if kind == "execute":
        deliver_agent_log(client, result.pop(AGENT_LOG_KEY, None))
    client.complete(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("execute", "engine", "prepare", "score"))
    args = parser.parse_args()
    try:
        # The public-repository pool (ops/public-runner-pool.md) dispatches only
        # an opaque job id; its engine and score jobs use sealed transfers end to end.
        public = os.environ.get("OBSERVER_POOL") == "public"
        if public and args.kind not in ("engine", "score"):
            raise JobError("public_pool_engine_only")
        client = JobClient(os.environ.get("OBSERVER_JOB_URL", ""), os.environ.get("OBSERVER_JOB_ID", ""),
                           None if public else os.environ.get("OBSERVER_JOB_NONCE", ""), GitHubIdentity(),
                           seal_key=SealKey() if public else None)
        with tempfile.TemporaryDirectory(prefix="observer-job-") as temporary:
            run_claimed(args.kind, client, Path(temporary))
        print("Observer job completed.")
        return 0
    except Exception:
        print("Observer job failed; check the private platform job status.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
