"""DB-backed job queue with an in-process worker pool."""
from __future__ import annotations

import hashlib
import logging
import shutil
import socket
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal, session_scope
from ..models import Evaluation, Job, Phase, Scenario, Submission, utcnow
from . import runner as runner_mod
from .scenarios import scenario_paths
from .scoring import InvalidSubmission, score_decisions

log = logging.getLogger("sac.jobs")
_stop = threading.Event()
_threads: list[threading.Thread] = []
_wake = threading.Event()


def enqueue(db: Session, kind: str, payload: dict) -> Job:
    job = Job(kind=kind, payload=payload)
    db.add(job)
    db.flush()
    _wake.set()
    return job


def _claim(db: Session, worker_id: str) -> Optional[Job]:
    job = db.query(Job).filter(Job.status == "queued").order_by(Job.created_at.asc(), Job.id.asc()).first()
    if job is None:
        return None
    res = db.execute(
        update(Job).where(Job.id == job.id, Job.status == "queued")
        .values(status="running", locked_by=worker_id, started_at=utcnow(), attempts=Job.attempts + 1)
    )
    db.commit()
    if res.rowcount != 1:
        return None
    db.refresh(job)
    return job


def run_job(db: Session, job: Job) -> None:
    if job.kind == "evaluate_submission":
        evaluate_submission(db, int(job.payload["submission_id"]))
    else:
        raise RuntimeError(f"unknown job kind {job.kind}")


def process_one(worker_id: str = "inline") -> bool:
    """Claim and run a single queued job. Returns False when the queue is empty."""
    db = SessionLocal()
    try:
        job = _claim(db, worker_id)
        if job is None:
            return False
        try:
            run_job(db, job)
            job.status = "done"
            job.error = ""
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", job.id)
            job.status = "failed"
            job.error = f"{exc}\n{traceback.format_exc()[-4000:]}"
            _mark_submission_failed(db, job, str(exc))
        job.finished_at = utcnow()
        db.commit()
        return True
    finally:
        db.close()


def _mark_submission_failed(db: Session, job: Job, message: str) -> None:
    sid = job.payload.get("submission_id")
    if not sid:
        return
    sub = db.get(Submission, int(sid))
    if sub and sub.status in ("queued", "running"):
        sub.status = "failed"
        sub.error = f"internal error: {message}"[:2000]
        sub.finished_at = utcnow()


def run_pending(max_jobs: int = 100) -> int:
    n = 0
    while n < max_jobs and process_one():
        n += 1
    return n


def _worker_loop(worker_id: str) -> None:
    while not _stop.is_set():
        try:
            if not process_one(worker_id):
                _wake.wait(timeout=2.0)
                _wake.clear()
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            time.sleep(2)


def start_workers(n: Optional[int] = None) -> None:
    s = get_settings()
    n = n or s.worker_concurrency
    _stop.clear()
    # reset jobs stuck in running from a previous crash
    with session_scope() as db:
        db.execute(update(Job).where(Job.status == "running").values(status="queued", locked_by=""))
        db.execute(update(Submission).where(Submission.status == "running").values(status="queued"))
    for i in range(n):
        t = threading.Thread(target=_worker_loop, args=(f"{socket.gethostname()}-{i}",), daemon=True, name=f"sac-worker-{i}")
        t.start()
        _threads.append(t)


def stop_workers() -> None:
    _stop.set()
    _wake.set()
    for t in _threads:
        t.join(timeout=5)
    _threads.clear()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _run_dir(sub: Submission, scn: Scenario) -> Path:
    return get_settings().runs_dir / f"sub-{sub.id}" / scn.slug


def evaluate_submission(db: Session, submission_id: int) -> None:
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise RuntimeError(f"submission {submission_id} not found")
    if sub.status == "cancelled":
        return
    sub.status = "running"
    sub.started_at = utcnow()
    sub.error = ""
    db.commit()

    phase = db.get(Phase, sub.phase_id)
    if sub.kind == "results":
        scenarios = [db.get(Scenario, sub.scenario_id)] if sub.scenario_id else []
    else:
        scenarios = [db.get(Scenario, sid) for sid in (phase.scenario_ids or [])]
        scenarios = [s for s in scenarios if s is not None and s.is_active]
    if not scenarios:
        sub.status = "failed"
        sub.error = "no evaluation scenario is configured for this phase"
        sub.finished_at = utcnow()
        db.commit()
        return

    # fresh evaluations
    for ev in list(sub.evaluations):
        db.delete(ev)
    db.flush()
    evals: list[Evaluation] = []
    for scn in scenarios:
        ev = Evaluation(submission_id=sub.id, scenario_id=scn.id, status="running")
        db.add(ev)
        evals.append(ev)
    db.commit()

    upload = Path(sub.file_path)
    agent_entry: Optional[Path] = None
    agent_dir: Optional[Path] = None
    if sub.kind == "agent":
        agent_dir = get_settings().runs_dir / f"sub-{sub.id}" / "agent"
        try:
            agent_entry = runner_mod.prepare_agent_dir(upload, agent_dir)
        except runner_mod.AgentPackageError as exc:
            for ev in evals:
                ev.status = "invalid"
                ev.error = str(exc)
                ev.finished_at = utcnow()
            sub.status = "invalid"
            sub.error = str(exc)
            sub.finished_at = utcnow()
            db.commit()
            return

    for ev, scn in zip(evals, scenarios):
        paths = scenario_paths(scn)
        out = _run_dir(sub, scn)
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        try:
            if sub.kind == "results":
                decisions = out / "decisions.csv"
                shutil.copyfile(upload, decisions)
            else:
                assert agent_entry is not None and agent_dir is not None
                result = runner_mod.run_agent(agent_entry, agent_dir, weather=paths["weather"], tiles=paths["tiles"], config=paths["config"], out_dir=out,
                                              scenario_meta={"slug": scn.slug, "name": scn.name, "n_slots": scn.n_slots, "n_nights": scn.n_nights})
                decisions = result.decisions_path
                ev.log_path = str(result.log_path)
                ev.summary = {"steps": result.steps, "agent_wall_seconds": round(result.wall_seconds, 3), "warnings": result.warnings[:20], "stderr_tail": result.stderr_tail[-2000:]}
            report_path = out / "score_report.json"
            metrics = score_decisions(paths["weather"], paths["tiles"], paths["config"], decisions, report_path)
            ev.status = "scored"
            ev.score = metrics["score"]
            ev.science_score = metrics["science_score"]
            ev.completion = metrics["completion"]
            ev.uniformity = metrics["uniformity"]
            ev.report_path = str(report_path)
            ev.decisions_path = str(decisions)
            ev.summary = {**(ev.summary or {}), **{k: v for k, v in metrics.items() if k != "region_completion"}, "region_completion": metrics["region_completion"]}
        except InvalidSubmission as exc:
            ev.status = "invalid"
            ev.error = str(exc)
        except runner_mod.AgentRunError as exc:
            ev.status = "failed"
            ev.error = str(exc)
            try:
                ev.log_path = str(out / "agent.log")
                ev.summary = {**(ev.summary or {}), "stderr_tail": (out / "agent.log").read_text(encoding="utf-8", errors="replace")[-2000:]}
            except OSError:
                pass
        ev.runtime_seconds = round(time.monotonic() - t0, 3)
        ev.finished_at = utcnow()
        db.commit()

    scored = [e for e in evals if e.status == "scored"]
    if len(scored) == len(evals):
        sub.status = "scored"
        sub.score = round(sum(e.score for e in scored) / len(scored), 6)
        sub.science_score = round(sum(e.science_score for e in scored) / len(scored), 6)
        sub.completion = round(sum(e.completion for e in scored) / len(scored), 6)
        sub.uniformity = round(sum(e.uniformity for e in scored) / len(scored), 6)
        sub.metrics = {"scenarios": [{"slug": s.slug, "score": e.score, "science_score": e.science_score, "completion": e.completion, "uniformity": e.uniformity} for e, s in zip(evals, scenarios)]}
    else:
        bad = [e for e in evals if e.status != "scored"]
        sub.status = "invalid" if all(e.status == "invalid" for e in bad) else "failed"
        sub.error = "; ".join(f"{s.slug}: {e.error}" for e, s in zip(evals, scenarios) if e.status != "scored")[:4000]
        sub.score = None
    sub.finished_at = utcnow()
    db.commit()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
