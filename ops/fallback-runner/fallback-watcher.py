#!/usr/bin/env python3
"""Starts the fallback runner VM on demand and stops it when idle (runs on the Mac).

The VM normally stays stopped and uses no memory. This watcher runs as a
LaunchAgent of the organizer's account and only reads GitHub metadata of the
fallback organization's observer-control repository with the organizer's
existing ``gh`` login: no participant code, no database credential. It

* starts the VM (as the ``observerfb`` account, via a sudoers rule limited to
  limactl) when a queued job of that repository waits for the
  ``observer-fallback`` label;
* stops it after ``--idle-minutes`` with no waiting job and an idle runner;
* boots it once a week while it is unused, so GitHub keeps the offline
  registration (self-hosted runners offline for 14 days are removed) and the
  runner can update itself.

    python3 fallback-watcher.py [--repository AGENTIC-OBSERVER26-runner-13/observer-control]

Standard library only; works with macOS's /usr/bin/python3 (3.9).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LABEL = "observer-fallback"
INSTANCE = "observer-fallback"
WAITING = {"queued", "waiting", "pending", "requested"}
MAINTENANCE_SECONDS = 7 * 86400


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S%z"), message, flush=True)


class GitHub:
    def __init__(self, gh: str, repository: str):
        self.gh, self.repository = gh, repository
        self.fallback_runs: dict[int, bool] = {}  # labels of a run's jobs never change

    def api(self, path: str):
        out = subprocess.run([self.gh, "api", f"repos/{self.repository}/{path}"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode:
            raise RuntimeError(f"gh api {path} failed: {out.stderr.strip()[:200]}")
        return json.loads(out.stdout)

    def waiting_jobs(self) -> int:
        """Jobs of queued runs that wait for the fallback label."""
        runs = self.api("actions/runs?status=queued&per_page=100")["workflow_runs"]
        ids = {r["id"] for r in runs}
        self.fallback_runs = {k: v for k, v in self.fallback_runs.items() if k in ids}
        waiting = 0
        for run in ids:
            if self.fallback_runs.get(run) is False:
                continue
            jobs = self.api(f"actions/runs/{run}/jobs?per_page=100")["jobs"]
            ours = [j for j in jobs if LABEL in j["labels"]]
            if jobs:  # a just-created run may not list its jobs yet
                self.fallback_runs[run] = bool(ours)
            waiting += sum(j["status"] in WAITING for j in ours)
        return waiting

    def runner(self, name: str):
        """(registered, online, busy) of the fallback runner."""
        for r in self.api("actions/runners?per_page=100")["runners"]:
            if r["name"] == name:
                return True, r["status"] == "online", bool(r["busy"])
        return False, False, False


class VM:
    def __init__(self, limactl: str, account: str):
        self.prefix = ["sudo", "-n", "-H", "-u", account, limactl]

    def run(self, *args: str, timeout: int = 60) -> str:
        out = subprocess.run([*self.prefix, *args], capture_output=True, text=True, timeout=timeout)
        if out.returncode:
            raise RuntimeError(f"limactl {args[0]} failed: {out.stderr.strip()[-300:]}")
        return out.stdout

    def status(self) -> str:
        for line in self.run("list", "--json").splitlines():
            if line.strip() and json.loads(line).get("name") == INSTANCE:
                return json.loads(line)["status"]
        raise RuntimeError(f"Lima instance {INSTANCE} does not exist for the VM account")

    def start(self) -> None:
        self.run("start", "--tty=false", INSTANCE, timeout=900)

    def stop(self) -> None:
        self.run("stop", INSTANCE, timeout=300)


class State:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str) -> float:
        return float(self.data.get(key, 0))

    def set(self, key: str, value: float) -> None:
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        tmp.replace(self.path)


def tick(github: GitHub, vm: VM, state: State, args, now: float) -> None:
    waiting = github.waiting_jobs()
    registered, online, busy = github.runner(args.runner_name)
    status = vm.status()
    if online or not state.get("last_online"):
        state.set("last_online", now)  # first run: the runner was just registered
    if status != "Running":
        # At most one maintenance boot a day, even if the runner never comes online.
        due = (registered and now - state.get("last_online") > MAINTENANCE_SECONDS
               and now - state.get("maintenance") > 86400)
        if waiting or due:
            if waiting:
                log(f"starting the VM: {waiting} waiting job(s)")
            else:
                log("starting the VM for weekly maintenance")
                state.set("maintenance", now)
            state.set("started", now)
            state.set("last_active", now)
            vm.start()
            log("VM started; waiting for the runner to come online")
        return
    if waiting and not online and now - state.get("started") > args.boot_minutes * 60:
        # Booted but the runner never connected (e.g. the VM hung): restart it once per boot window.
        log(f"runner still offline {args.boot_minutes} minutes after the VM started; restarting the VM")
        state.set("started", now)
        vm.stop()
        vm.start()
        return
    if waiting or busy or not state.get("last_active"):
        # Not active before means the VM was found running (e.g. after a
        # watcher restart): it gets a full idle period too.
        state.set("last_active", now)
        return
    if now - state.get("last_active") < args.idle_minutes * 60:
        return
    # Re-check right before stopping: a job may have arrived during this tick.
    if github.waiting_jobs() or github.runner(args.runner_name)[2]:
        state.set("last_active", now)
        return
    log(f"stopping the VM after {args.idle_minutes} idle minutes")
    vm.stop()
    state.set("last_active", 0)
    log("VM stopped")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default="AGENTIC-OBSERVER26-runner-13/observer-control")
    parser.add_argument("--runner-name", default="observer-fallback-1")
    parser.add_argument("--account", default="observerfb", help="macOS account that owns the Lima VM")
    parser.add_argument("--limactl", default="/opt/homebrew/bin/limactl")
    parser.add_argument("--gh", default="/opt/homebrew/bin/gh")
    parser.add_argument("--interval", type=int, default=30, help="seconds between checks")
    parser.add_argument("--idle-minutes", type=int, default=15)
    parser.add_argument("--boot-minutes", type=int, default=10,
                        help="restart the VM when its runner is still offline this long after a start")
    parser.add_argument("--state", type=Path,
                        default=Path.home() / "Library/Application Support/observer-fallback/watcher-state.json")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    args = parser.parse_args()
    github, vm, state = GitHub(args.gh, args.repository), VM(args.limactl, args.account), State(args.state)
    log(f"watching {args.repository} for jobs labelled {LABEL} (idle stop after {args.idle_minutes} min)")
    while True:
        try:
            tick(github, vm, state, args, time.time())
        except Exception as error:  # noqa: BLE001 - never die; launchd would restart in a loop
            # Unknown state never stops the VM: a running job must not be cut off.
            log(f"check failed: {error}")
            if args.once:
                sys.exit(1)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    os.umask(0o077)
    main()
