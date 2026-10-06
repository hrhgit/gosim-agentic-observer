from __future__ import annotations

import os
from pathlib import Path
import subprocess
import uuid

import pytest

from project_platform.package import ProjectFile, project_digest
from project_platform.repository import RepositoryError, SnapshotRepository


class LocalSnapshot(SnapshotRepository):
    """Real Git object store; only network transport is replaced by a local bare repo."""
    def __init__(self, remote: Path):
        super().__init__("AGENTIC-OBSERVER26-runner-1/participant-"+"a"*32,"test-repository-key")
        self.local=remote
        self.commands=[]

    def _git(self,root,*args):
        self.commands.append(args)
        if args[0]=="ls-remote":
            return subprocess.run(["git","ls-remote","--refs",str(self.local),args[-1]],
                                  cwd=root,capture_output=True,text=True,check=True).stdout.strip()
        if args[0]=="push":
            # The local test does not authenticate; production always uses HTTPS.
            return subprocess.run(["git","-c","core.hooksPath=/dev/null","push","--porcelain",str(self.local),args[-1]],
                                  cwd=root,capture_output=True,text=True,check=True).stdout.strip()
        return super()._git(root,*args)


@pytest.fixture
def repository(tmp_path):
    remote=tmp_path/"remote.git"
    subprocess.run(["git","init","--bare",str(remote)],capture_output=True,check=True)
    return LocalSnapshot(remote)


def test_snapshot_preserves_ignored_files_binary_bytes_line_endings_and_mode(repository):
    files=[
        ProjectFile(".gitignore",b"agent.py\n"),
        ProjectFile(".gitattributes",b"*.py text eol=lf\n*.bin filter=private\n"),
        ProjectFile("agent.py",b"#!/usr/bin/python3\r\nprint('hello')\r\n",True),
        ProjectFile("model.bin",bytes(range(256))),
    ]
    revision=str(uuid.uuid4())
    commit,digest=repository.store_revision(revision,files)
    assert digest==project_digest(files)
    assert repository.store_revision(revision,files)==(commit,digest)
    assert sum(cmd[0]=="push" for cmd in repository.commands)==1
    for file in files:
        content=subprocess.run(["git","--git-dir",str(repository.local),"show",commit+":"+file.path],
                               capture_output=True,check=True).stdout
        assert content==file.data
    tree=subprocess.run(["git","--git-dir",str(repository.local),"ls-tree",commit,"agent.py"],
                        capture_output=True,check=True,text=True).stdout
    assert tree.startswith("100755")
    assert all("--force" not in cmd for cmd in repository.commands if cmd[0]=="push")


def test_changed_source_cannot_overwrite_a_frozen_revision(repository):
    revision=str(uuid.uuid4())
    repository.store_revision(revision,[ProjectFile("agent.py",b"first")])
    with pytest.raises(RepositoryError,match="immutable revision"):
        repository.store_revision(revision,[ProjectFile("agent.py",b"second")])


def test_prepared_and_result_snapshots_are_separate_immutable_references(repository):
    identifier=str(uuid.uuid4())
    source,_=repository.store_revision(identifier,[ProjectFile('agent.py',b'original')])
    prepared,_=repository.store_snapshot('prepared',identifier,[ProjectFile('agent.py',b'original'),ProjectFile('.observer-adapter/run.py',b'interface')])
    result,_=repository.store_snapshot('results',identifier,[ProjectFile('decisions.csv',b'only a trace')])
    assert len({source,prepared,result})==3
    refs=repository._git(repository.local,'ls-remote','--refs',repository.remote,'refs/heads/*')
    assert all('refs/heads/'+kind+'/'+identifier in refs for kind in ('revisions','prepared','results'))
    assert repository.store_snapshot('results',identifier,[ProjectFile('decisions.csv',b'only a trace')])[0]==result
    with pytest.raises(RepositoryError,match='immutable revision'):
        repository.store_snapshot('results',identifier,[ProjectFile('decisions.csv',b'forged replacement')])


def test_global_git_hooks_filters_and_secrets_do_not_enter_snapshot_environment(monkeypatch,repository,tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN","host-platform-secret")
    monkeypatch.setenv("GIT_CONFIG_COUNT","9")
    monkeypatch.setenv("GIT_SSH_COMMAND","malicious-hook")
    worker=SnapshotRepository("AGENTIC-OBSERVER26-runner-2/participant-"+"b"*32,"scoped-secret")
    assert "GITHUB_TOKEN" not in worker.environment
    assert "GIT_SSH_COMMAND" not in worker.environment
    assert worker.environment["GIT_CONFIG_COUNT"]=="1"
    assert worker.environment["GIT_CONFIG_GLOBAL"]==os.devnull
    assert "scoped-secret" not in worker.remote
    assert worker.environment["GIT_TERMINAL_PROMPT"]=="0"


class FlakyRemote(LocalSnapshot):
    """GitHub refuses the first attempt of each remote operation (e.g. a token not yet usable)."""
    remote_retry_delays=(0,0,0)

    def __init__(self,remote,failures=1):
        super().__init__(remote)
        self.failures={"ls-remote":failures,"push":failures}

    def _git(self,root,*args):
        if self.failures.get(args[0],0):
            self.failures[args[0]]-=1
            self.commands.append(args)
            raise RepositoryError("Repository operation failed.")
        return super()._git(root,*args)


def test_transient_remote_failures_are_retried_without_changing_the_snapshot(tmp_path):
    remote=tmp_path/"remote.git"
    subprocess.run(["git","init","--bare",str(remote)],capture_output=True,check=True)
    flaky=FlakyRemote(remote)
    identifier=str(uuid.uuid4())
    commit,_=flaky.store_snapshot('prepared',identifier,[ProjectFile('agent.py',b'x')])
    refs=subprocess.run(["git","--git-dir",str(remote),"rev-parse","refs/heads/prepared/"+identifier],
                        capture_output=True,check=True,text=True).stdout.strip()
    assert refs==commit
    assert [cmd[0] for cmd in flaky.commands if cmd[0] in ("ls-remote","push")]==["ls-remote"]*2+["push"]*2+["ls-remote"]


def test_persistent_remote_failure_is_reported_as_platform_storage(tmp_path):
    from project_platform.diagnostics import safe_code
    remote=tmp_path/"remote.git"
    subprocess.run(["git","init","--bare",str(remote)],capture_output=True,check=True)
    flaky=FlakyRemote(remote,failures=99)
    with pytest.raises(RepositoryError) as caught:
        flaky.store_snapshot('prepared',str(uuid.uuid4()),[ProjectFile('agent.py',b'x')])
    assert sum(cmd[0]=="ls-remote" for cmd in flaky.commands)==4
    assert safe_code(caught.value)=='snapshot_repository_unavailable'
