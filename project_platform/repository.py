"""Store validated source snapshots using Git, without executing project files.

Runs only in the trusted preparation job. The repository-scoped credential is
never passed to a build container, participant process, archive or command line.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Iterable
import uuid

from .manifest import ProjectError
from .package import ProjectFile, extract_project, project_digest

# Runner organizations 1-99; the backend decides which are enabled.
REPOSITORY = re.compile(r"^AGENTIC-OBSERVER26-runner-(?:[1-9]|[1-9][0-9])/participant-[0-9a-f]{32}$")
SHA = re.compile(r"^[0-9a-f]{40}$")


class RepositoryError(RuntimeError):
    pass


class SnapshotRepository:
    """A private repository provisioned with Actions disabled by the control API."""

    # Remote operations are retried: a freshly minted repository token or a
    # transient GitHub error must not fail a whole preparation or scoring job.
    remote_retry_delays: tuple[float, ...] = (1.0, 3.0, 6.0)

    def __init__(self, full_name: str, token: str, *, timeout: float = 120):
        if not REPOSITORY.fullmatch(full_name) or not token or any(c in token for c in "\r\n\x00"):
            raise ProjectError("Invalid private snapshot repository.")
        self.full_name = full_name
        self.remote = "https://github.com/" + full_name + ".git"
        self.token = token
        self.timeout = timeout
        authorization = "Basic " + base64.b64encode(("x-access-token:" + token).encode()).decode()
        self._redactions = (token, authorization)
        # Ignore global/system hooks, credentials, URL rewriting, external filters
        # and inherited GIT_* variables. Do not repurpose the user's HOME.
        self.environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "SSL_CERT_FILE") if key in os.environ}
        self.environment.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "Authorization: " + authorization,
            "GIT_AUTHOR_NAME": "Agentic Observer", "GIT_AUTHOR_EMAIL": "observer@create.gosim.org",
            "GIT_COMMITTER_NAME": "Agentic Observer", "GIT_COMMITTER_EMAIL": "observer@create.gosim.org",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        })

    def _git(self, root: Path, *args: str) -> str:
        command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.autocrlf=false",
                   "-c", "core.safecrlf=false", "-c", "commit.gpgsign=false",
                   "-c", "credential.helper=", "-c", "protocol.file.allow=never", *args]
        try:
            result = subprocess.run(command, cwd=root, env=self.environment, capture_output=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RepositoryError("Repository operation could not complete.") from exc
        if result.returncode:
            # Never return arbitrary git/server diagnostics or a credential-bearing URL.
            raise RepositoryError("Repository operation failed.")
        return result.stdout.decode("utf-8", "strict").strip()

    def _remote(self, root: Path, *args: str) -> str:
        for delay in self.remote_retry_delays:
            try:
                return self._git(root, *args)
            except RepositoryError:
                time.sleep(delay)
        return self._git(root, *args)

    def store_revision(self, revision_id: str, files: Iterable[ProjectFile]) -> tuple[str,str]:
        return self.store_snapshot('revisions', revision_id, files)

    def store_snapshot(self, kind: str, identifier: str, files: Iterable[ProjectFile]) -> tuple[str,str]:
        if kind not in ('revisions', 'prepared', 'results'):
            raise ProjectError('Invalid immutable snapshot kind.')
        revision = str(uuid.UUID(identifier))
        items = tuple(files)
        digest = project_digest(items)
        ref = "refs/heads/" + kind + "/" + revision
        with tempfile.TemporaryDirectory(prefix="observer-snapshot-") as tmp:
            root = Path(tmp) / "source"
            extract_project(items, root)
            self._git(root, "init", "--initial-branch=source")
            # Preserve the archive bytes exactly: repository attributes must not
            # normalize line endings, expand identifiers or transcode a source.
            (root / ".git" / "info" / "attributes").write_text("* -text -filter -ident -working-tree-encoding\n")
            # A complete uploaded project may intentionally include files ignored
            # by its development checkout; storing it must not silently omit them.
            self._git(root, "add", "--force", "--all", "--", ".")
            label = {'revisions':'Source revision', 'prepared':'Prepared project', 'results':'Evaluation result'}[kind]
            self._git(root, "commit", "-m", label + " " + revision + "\n\nSHA256 " + digest)
            commit = self._git(root, "rev-parse", "HEAD")
            if not SHA.fullmatch(commit):
                raise RepositoryError("Repository returned an invalid commit.")
            existing = self._remote(root, "ls-remote", "--refs", self.remote, ref)
            if existing:
                if existing.split()[0] != commit:
                    raise RepositoryError("An immutable revision already exists with different contents.")
                return commit, digest
            # A new branch, never a force push or a change to the repository's default
            # branch. Concurrent identical preparations produce the same commit.
            self._remote(root, "push", "--porcelain", self.remote, "HEAD:" + ref)
            verified = self._remote(root, "ls-remote", "--refs", self.remote, ref)
            if not verified or verified.split()[0] != commit:
                raise RepositoryError("Source revision could not be verified.")
            return commit, digest
