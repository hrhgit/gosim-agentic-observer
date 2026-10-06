"""On-demand start and idle stop of the self-hosted fallback VM (ops/fallback-runner/fallback-watcher.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    'fallback_watcher', Path(__file__).resolve().parents[1] / 'ops/fallback-runner/fallback-watcher.py')
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)

ARGS = SimpleNamespace(runner_name='observer-fallback-1', idle_minutes=15, boot_minutes=10)
HOSTED = ['ubuntu-24.04']
FALLBACK = ['self-hosted', 'linux', 'observer-fallback']


class FakeGitHub(watcher.GitHub):
    def __init__(self):
        super().__init__('gh', 'AGENTIC-OBSERVER26-runner-13/observer-control')
        self.runs, self.runner_state, self.calls = {}, (True, False, False), []

    def api(self, path):
        self.calls.append(path)
        if path.startswith('actions/runs?'):
            return {'workflow_runs': [{'id': r} for r in self.runs]}
        if path.startswith('actions/runs/'):
            run = int(path.split('/')[2])
            return {'jobs': [{'status': status, 'labels': labels} for status, labels in self.runs[run]]}
        registered, online, busy = self.runner_state
        return {'runners': [{'name': 'observer-fallback-1', 'status': 'online' if online else 'offline',
                             'busy': busy}] if registered else []}


class FakeVM:
    def __init__(self, status='Stopped'):
        self.state, self.actions = status, []

    def status(self):
        return self.state

    def start(self):
        self.actions.append('start'); self.state = 'Running'

    def stop(self):
        self.actions.append('stop'); self.state = 'Stopped'


@pytest.fixture
def env(tmp_path):
    return FakeGitHub(), FakeVM(), watcher.State(tmp_path / 'state.json')


def test_a_waiting_fallback_job_starts_the_vm_and_hosted_jobs_do_not(env):
    github, vm, state = env
    github.runs = {1: [('queued', HOSTED)], 2: []}
    watcher.tick(github, vm, state, ARGS, 1000)
    assert vm.actions == []
    github.runs[3] = [('queued', FALLBACK)]
    watcher.tick(github, vm, state, ARGS, 1030)
    assert vm.actions == ['start']
    # A run with hosted jobs is inspected once; a run that listed no jobs yet is asked again.
    assert github.calls.count('actions/runs/1/jobs?per_page=100') == 1
    assert github.calls.count('actions/runs/2/jobs?per_page=100') == 2


def test_the_vm_stops_only_after_the_idle_period_with_an_idle_runner(env):
    github, vm, state = env
    github.runs = {7: [('queued', FALLBACK)]}
    watcher.tick(github, vm, state, ARGS, 0)
    github.runs, github.runner_state = {7: [('in_progress', FALLBACK)]}, (True, True, True)
    for now in (60, 600, 1200):
        watcher.tick(github, vm, state, ARGS, now)  # busy: never idle
    github.runs, github.runner_state = {}, (True, True, False)
    watcher.tick(github, vm, state, ARGS, 1200 + 14 * 60)
    assert vm.actions == ['start']
    watcher.tick(github, vm, state, ARGS, 1200 + 15 * 60)
    assert vm.actions == ['start', 'stop']


def test_a_job_arriving_while_stopping_keeps_the_vm_running(env, monkeypatch):
    github, vm, state = env
    vm.state = 'Running'
    github.runner_state = (True, True, False)
    watcher.tick(github, vm, state, ARGS, 0)  # found running: full grace period
    checks = iter([0, 1])
    monkeypatch.setattr(github, 'waiting_jobs', lambda: next(checks))
    watcher.tick(github, vm, state, ARGS, 16 * 60)
    assert vm.actions == [] and state.get('last_active') == 16 * 60


def test_weekly_maintenance_boot_and_restart_of_a_hung_vm(env):
    github, vm, state = env
    watcher.tick(github, vm, state, ARGS, 100)  # first run after registration
    assert vm.actions == []
    watcher.tick(github, vm, state, ARGS, 100 + 7 * 86400 + 1)
    assert vm.actions == ['start']
    # The runner never came online: the idle VM stops, and is not booted again the same day.
    for minutes in (1, 16, 17, 60):
        watcher.tick(github, vm, state, ARGS, 100 + 7 * 86400 + minutes * 60)
    assert vm.actions == ['start', 'stop']
    watcher.tick(github, vm, state, ARGS, 100 + 8 * 86400 + 2)
    assert vm.actions == ['start', 'stop', 'start']
    github.runs = {9: [('queued', FALLBACK)]}
    vm.actions.clear()
    watcher.tick(github, vm, state, ARGS, 100 + 8 * 86400 + 11 * 60)
    assert vm.actions == ['stop', 'start']


def test_unknown_state_never_stops_the_vm(env, monkeypatch):
    github, vm, state = env
    vm.state = 'Running'
    state.set('last_active', 1)

    def broken(path):
        raise RuntimeError('gh api failed')
    monkeypatch.setattr(github, 'api', broken)
    with pytest.raises(RuntimeError):
        watcher.tick(github, vm, state, ARGS, 10 ** 6)
    assert vm.actions == []
