"""Agent runner: drives a participant agent through the observer step protocol inside a sandbox.

Protocol (observer-v1), newline-delimited JSON over stdin/stdout:
  platform -> agent : {"type": "init", ...}            (once)
  platform -> agent : {"type": "step", ...}            (repeated)
  agent -> platform : {"action": "observe"|"wait", "tile_id": ..., "program": ..., "reason": ...}
  platform -> agent : {"type": "end", ...}             (once)

The runner applies each answer to the frozen ScoreEngine, records the decision, and finally
writes decisions.csv which is re-scored by the frozen scorer for the official report.
"""
from __future__ import annotations

import json
import os
import queue
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from scoring import scorer
from scoring.protocol import (PROTOCOL, ProtocolError, apply_and_record, build_init, build_step, decision_row,
                              normalise_decision, parse_answer)

from ..config import get_settings
from .scenarios import DECISION_FIELDS, write_csv

ENTRY_CANDIDATES = ("agent.py", "main.py")


class AgentPackageError(ValueError):
    pass


class AgentRunError(RuntimeError):
    """Agent crashed / timed out / violated the protocol. Message goes to the participant."""


@dataclass
class RunResult:
    decisions_path: Path
    log_path: Path
    steps: int
    wall_seconds: float
    report: dict
    stderr_tail: str = ""
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Package handling
# ---------------------------------------------------------------------------

def prepare_agent_dir(upload: Path, dest: Path, *, max_files: int = 2000, max_bytes: int = 50 * 1024 * 1024) -> Path:
    """Extract an uploaded .py or .zip into dest safely. Returns the entry script path."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if upload.suffix.lower() == ".py":
        shutil.copyfile(upload, dest / "agent.py")
        return dest / "agent.py"
    if not zipfile.is_zipfile(upload):
        raise AgentPackageError("upload must be a .py file or a .zip archive")
    total = 0
    with zipfile.ZipFile(upload) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if len(members) > max_files:
            raise AgentPackageError(f"archive has more than {max_files} files")
        for m in members:
            name = m.filename
            if name.startswith("__MACOSX/") or os.path.basename(name).startswith("._"):
                continue
            norm = os.path.normpath(name)
            if norm.startswith("..") or os.path.isabs(norm) or norm.startswith("/") or ":" in norm.split("/")[0]:
                raise AgentPackageError(f"unsafe path in archive: {name}")
            # symlinks (unix mode bits) are rejected
            if (m.external_attr >> 16) & 0o170000 == 0o120000:
                raise AgentPackageError(f"symlinks are not allowed: {name}")
            total += m.file_size
            if total > max_bytes:
                raise AgentPackageError("archive exceeds the 50 MB uncompressed limit")
            target = dest / norm
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)
    # locate entry: root, or single top-level folder
    for cand in ENTRY_CANDIDATES:
        if (dest / cand).is_file():
            return dest / cand
    subdirs = [p for p in dest.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if len(subdirs) == 1:
        for cand in ENTRY_CANDIDATES:
            if (subdirs[0] / cand).is_file():
                return subdirs[0] / cand
    raise AgentPackageError("archive must contain agent.py (or main.py) at its root")


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

def _limit_resources(memory_mb: int, cpu_seconds: int):
    def _apply():
        os.setsid()
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        except (ValueError, OSError):
            pass
        if sys.platform.startswith("linux"):
            try:
                resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024))
            except (ValueError, OSError):
                pass
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
            except (ValueError, OSError):
                pass
    return _apply


class Sandbox:
    """Launches the agent process according to settings.sandbox_mode."""

    def __init__(self, entry: Path, workdir: Path, log_file):
        s = get_settings()
        self.settings = s
        self.entry = entry
        self.workdir = workdir
        self.log_file = log_file
        self.proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self.stdout_bytes = 0

    def start(self) -> None:
        s = self.settings
        scratch = self.workdir / "scratch"
        scratch.mkdir(exist_ok=True)
        if s.sandbox_mode == "docker":
            cmd = [
                "docker", "run", "--rm", "-i", "--network", "none",
                "--memory", f"{s.agent_memory_mb}m", "--cpus", "1", "--pids-limit", "64",
                "--read-only", "--tmpfs", "/tmp:rw,size=64m", "--tmpfs", "/agent/scratch:rw,size=64m",
                "-v", f"{self.workdir}:/agent:ro", "-w", "/agent",
                "-e", "PYTHONUNBUFFERED=1", "-e", f"OBSERVER_PROTOCOL={PROTOCOL}",
                s.docker_image, "python", "-I", "-B", str(self.entry.relative_to(self.workdir)),
            ]
            preexec = None
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        else:
            cmd = [s.agent_python, "-I", "-B", str(self.entry)]
            preexec = _limit_resources(s.agent_memory_mb, s.agent_timeout_seconds)
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(scratch), "TMPDIR": str(scratch), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "OBSERVER_PROTOCOL": PROTOCOL,
                "OBSERVER_SCRATCH": str(scratch),
            }
        self.proc = subprocess.Popen(
            cmd, cwd=str(self.workdir), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log_file, preexec_fn=preexec, text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            for line in self.proc.stdout:
                self.stdout_bytes += len(line)
                if self.stdout_bytes > 32 * 1024 * 1024:
                    self._lines.put(None)
                    return
                self._lines.put(line)
        except Exception:
            pass
        finally:
            self._lines.put(None)

    def send(self, message: dict) -> None:
        assert self.proc and self.proc.stdin
        try:
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AgentRunError("agent closed its input stream (crashed?)") from exc

    def recv(self, timeout: float) -> str:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty as exc:
            raise AgentRunError(f"agent did not answer within {timeout:.0f}s for one step") from exc
        if line is None:
            raise AgentRunError("agent exited before answering")
        return line

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._kill()
        try:
            if self.proc.stdout:
                self.proc.stdout.close()
        except Exception:
            pass

    def _kill(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            if self.settings.sandbox_mode == "docker":
                self.proc.kill()
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_agent(entry: Path, workdir: Path, *, weather: Path, tiles: Path, config: Path, out_dir: Path,
              scenario_meta: Optional[dict] = None) -> RunResult:
    s = get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    decisions_path = out_dir / "decisions.csv"
    log_path = out_dir / "agent.log"
    cfg = scorer.load_config(config)
    raw_config = json.loads(config.read_text(encoding="utf-8"))
    weather_slots = scorer.load_weather(weather, cfg)
    tile_map = scorer.load_tiles(tiles)
    engine = scorer.ScoreEngine(cfg, weather_slots, tile_map)
    limits = {"step_timeout_seconds": s.agent_step_timeout_seconds, "total_timeout_seconds": s.agent_timeout_seconds, "max_steps": s.agent_max_steps}
    rows: list[dict] = []
    warnings: list[str] = []
    deadline = time.monotonic() + s.agent_timeout_seconds
    started = time.monotonic()
    steps = 0
    last_action: Optional[dict] = None
    with log_path.open("w", encoding="utf-8") as log_file:
        box = Sandbox(entry, workdir, log_file)
        try:
            box.start()
            box.send(build_init(cfg, raw_config, tile_map, scenario_meta or {}, limits))
            while True:
                state = build_step(engine, steps, last_action)
                if state is None:
                    break
                if steps >= s.agent_max_steps:
                    raise AgentRunError(f"agent exceeded the maximum of {s.agent_max_steps} steps")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AgentRunError(f"agent exceeded the total time limit of {s.agent_timeout_seconds}s")
                box.send(state)
                try:
                    answer = parse_answer(box.recv(min(s.agent_step_timeout_seconds, max(remaining, 0.1))))
                except ProtocolError as exc:
                    raise AgentRunError(str(exc)) from exc
                decision, warning = normalise_decision(answer, steps, state["now"]["slot_id"], tile_map)
                if warning:
                    warnings.append(warning)
                try:
                    last_action = apply_and_record(engine, decision)
                except scorer.ScoringError as exc:
                    raise AgentRunError(f"step {steps}: {exc}") from exc
                rows.append(decision_row(decision))
                steps += 1
            engine.finish()
            try:
                box.send({"type": "end", "summary": {"steps": steps, "completed_tiles": len(engine.completed_tiles), "science_score": round(engine.science_score, 6)}})
            except AgentRunError:
                pass
        finally:
            box.stop()
    write_csv(decisions_path, DECISION_FIELDS, rows)
    wall = time.monotonic() - started
    report = engine.report()
    tail = ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        tail = text[-4000:]
    except OSError:
        pass
    return RunResult(decisions_path=decisions_path, log_path=log_path, steps=steps, wall_seconds=wall, report=report, stderr_tail=tail, warnings=warnings)
