"""Run a participant agent package against a v3 scenario with the challenge workflow, inside the platform sandbox.

Agent package layout (zip root or single top-level folder):
    <entry>.py            minimal_agent.py | agent.py | main.py   (first found)
    requirements.txt      optional; installed into a per-run virtualenv (network allowed only during installation)
    .env                  optional; loaded into the agent's environment only (never logged, never uploaded)
    any other files       importable from the entry script's directory

Hidden weather never leaves the scenario directory: the agent process runs with cwd = its own package directory,
a scrubbed environment, and (in docker mode) a read-only bind mount of that directory only.
"""
from __future__ import annotations

import json
import os
import re
import resource
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from challenge.challenge_workflow import ChallengeWorkflow, GlobalDeadlineExpired
from challenge.contracts import LEGACY_PARTICIPANT_PROTOCOL_VERSION, PARTICIPANT_PROTOCOL_VERSION
from challenge.run_challenge import JsonLineAgentProcess

from .config import get_settings

ENTRY_CANDIDATES = ("minimal_agent.py", "agent.py", "main.py")
SAFE_ENV_KEYS = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class AgentPackageError(ValueError):
    pass


class AgentRunError(RuntimeError):
    pass


def find_entry(agent_dir: Path) -> Path:
    for cand in ENTRY_CANDIDATES:
        if (agent_dir / cand).is_file():
            return agent_dir / cand
    subdirs = [p for p in agent_dir.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != "scratch"]
    if len(subdirs) == 1:
        for cand in ENTRY_CANDIDATES:
            if (subdirs[0] / cand).is_file():
                return subdirs[0] / cand
    raise AgentPackageError("package must contain minimal_agent.py, agent.py or main.py at its root")


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (no interpolation). Values are kept out of logs by the caller."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if SAFE_ENV_KEYS.match(key):
            env[key] = value
    return env


def _limits(memory_mb: int, cpu_seconds: int):
    def apply():
        os.setsid()
        for res, val in ((resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 30)), (resource.RLIMIT_FSIZE, (256 << 20, 256 << 20)), (resource.RLIMIT_NOFILE, (512, 512))):
            try:
                resource.setrlimit(res, val)
            except (ValueError, OSError):
                pass
        if sys.platform.startswith("linux"):
            for res, val in ((resource.RLIMIT_AS, (memory_mb << 20, memory_mb << 20)), (resource.RLIMIT_NPROC, (128, 128))):
                try:
                    resource.setrlimit(res, val)
                except (ValueError, OSError):
                    pass
    return apply


def prepare_environment(agent_dir: Path, entry: Path, log) -> tuple[str, dict]:
    """Create a per-run virtualenv when the package ships requirements.txt. Returns (python, stats)."""
    s = get_settings()
    stats = {"venv": False, "requirements": 0}
    req = entry.parent / "requirements.txt"
    python = s.agent_python
    if req.is_file() and s.sandbox_mode != "docker":
        lines = [l.strip() for l in req.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip() and not l.startswith("#")]
        stats["requirements"] = len(lines)
        if lines:
            venv = agent_dir / ".venv"
            subprocess.run([python, "-m", "venv", str(venv)], check=True, capture_output=True, timeout=300)
            pip = venv / "bin" / "pip"
            proc = subprocess.run([str(pip), "install", "--disable-pip-version-check", "--no-input", "-r", str(req)],
                                  capture_output=True, text=True, timeout=s.install_timeout_seconds)
            log.write(f"[platform] pip install -r requirements.txt exit={proc.returncode}\n{proc.stdout[-4000:]}{proc.stderr[-4000:]}\n")
            if proc.returncode != 0:
                raise AgentRunError("requirements.txt could not be installed (see agent.log)")
            python = str(venv / "bin" / "python")
            stats["venv"] = True
    return python, stats


class SandboxedAgentProcess(JsonLineAgentProcess):
    """JsonLineAgentProcess with the platform's isolation: scrubbed env, own cwd, rlimits, stderr to a log, optional docker."""

    def __init__(self, command: list[str], *, agent_dir: Path, env: dict[str, str], log_file, initialization_timeout_seconds: float = 30.0,
                 protocol_version: str | None = None):
        super().__init__(command, initialization_timeout_seconds,
                         **({"protocol_version": protocol_version} if protocol_version else {}))
        self.agent_dir = agent_dir
        self.env = env
        self.log_file = log_file
        self.stdout_bytes = 0

    def _start(self):
        if self.process is None:
            s = get_settings()
            if s.sandbox_mode == "docker":
                net = ["--network", "none"] if s.agent_network == "none" else []
                cmd = ["docker", "run", "--rm", "-i", *net, "--memory", f"{s.agent_memory_mb}m", "--cpus", "1", "--pids-limit", "128",
                       "--read-only", "--tmpfs", "/tmp:rw,size=256m", "-v", f"{self.agent_dir}:/agent:ro", "-w", "/agent"]
                for k, v in self.env.items():
                    cmd += ["-e", f"{k}={v}"]
                cmd += [s.docker_image, *self.command]
                self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log_file, text=False, bufsize=0)
                self._configure_pipes(self.process)
            else:
                self.process = subprocess.Popen(self.command, cwd=str(self.agent_dir), env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=self.log_file, text=False, bufsize=0, preexec_fn=_limits(s.agent_memory_mb, s.agent_cpu_seconds))
                self._configure_pipes(self.process)
        return self.process

    def _write_message(self, message, deadline_monotonic):
        try:
            super()._write_message(message, deadline_monotonic)
        except BrokenPipeError as exc:
            code = self.process.poll() if self.process else None
            raise RuntimeError(f"agent exited before reading the next message (exit code {code})") from exc

    def __call__(self, snapshot, deadline_monotonic):
        result = super().__call__(snapshot, deadline_monotonic)
        self.stdout_bytes += len(self._stdout_buffer)
        return result

    def close(self, force: bool = False) -> None:
        proc = self.process
        if proc is not None and proc.poll() is None and get_settings().sandbox_mode != "docker":
            try:
                os.killpg(os.getpgid(proc.pid), 15 if not force else 9)
            except Exception:
                pass
        super().close(force=force)


def run_agent_package(agent_dir: Path, scenario_root: Path, out_dir: Path, *, wallclock_seconds: float, scenario_meta: Optional[dict] = None) -> dict:
    """Run the workflow with the packaged agent. Returns the workflow result (dict) and writes decisions.csv, workflow_result.json, agent.log."""
    s = get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    entry = find_entry(agent_dir)
    scratch = agent_dir / "scratch"
    scratch.mkdir(exist_ok=True)
    log_path = out_dir / "agent.log"
    with log_path.open("w", encoding="utf-8") as log:
        python, env_stats = prepare_environment(agent_dir, entry, log)
        dotenv = load_dotenv(entry.parent / ".env")
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(scratch), "TMPDIR": str(scratch), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
               "SAC_SCENARIO": (scenario_meta or {}).get("slug", ""),
               "SAC_WALLCLOCK_SECONDS": str(int(wallclock_seconds))}
        if s.agent_proxy:
            env.update({"HTTPS_PROXY": s.agent_proxy, "HTTP_PROXY": s.agent_proxy, "https_proxy": s.agent_proxy, "http_proxy": s.agent_proxy})
        for k, v in dotenv.items():
            if k not in ("PATH", "HOME", "TMPDIR", "LD_PRELOAD", "PYTHONPATH", "PYTHONSTARTUP"):
                env[k] = v
        note = agent_dir / "PLATFORM_NOTE.txt"
        if note.is_file():
            log.write("[platform] " + note.read_text().strip() + "\n")
        log.write(f"[platform] entry={entry.name} python={'venv' if env_stats['venv'] else 'platform'} dotenv_keys={sorted(dotenv)} network={s.agent_network} wallclock={wallclock_seconds}s\n")
        log.flush()
        if s.sandbox_mode == "docker":
            command = ["python", "-B", str(entry.relative_to(agent_dir))]
        else:
            command = [python, "-B", str(entry)]
        workflow = ChallengeWorkflow(root=scenario_root)
        protocol_version = PARTICIPANT_PROTOCOL_VERSION if workflow.mechanics else LEGACY_PARTICIPANT_PROTOCOL_VERSION
        env["PARTICIPANT_PROTOCOL"] = protocol_version
        provider = SandboxedAgentProcess(command, agent_dir=agent_dir, env=env, log_file=log,
                                         initialization_timeout_seconds=s.agent_init_timeout_seconds, protocol_version=protocol_version)
        started = time.monotonic()
        try:
            result = workflow.run(provider, wallclock_seconds=wallclock_seconds)
        finally:
            provider.close(force=True)
            log.write(f"[platform] finished in {time.monotonic() - started:.1f}s\n")
    workflow.write_outputs(out_dir, result)
    # keep workflow_result.json small for storage: the initial publication is reproducible from the scenario
    wr = out_dir / "workflow_result.json"
    if wr.exists():
        data = json.loads(wr.read_text(encoding="utf-8"))
        data.pop("initial_publication", None)
        wr.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    result.pop("initial_publication", None)
    return result
