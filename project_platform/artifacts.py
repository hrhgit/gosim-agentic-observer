"""Bounded private artifact transfers for trusted jobs; no archive extraction shortcuts."""
from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path

from .job_client import Http, JobError, retrying
from .package import MAX_ARCHIVE_BYTES, MAX_EXPANDED_BYTES, ProjectFile, read_project_zip, validate_files
from .repository import SnapshotRepository


def download_project(http: Http, url: str, digest: str | None = None) -> tuple[ProjectFile, ...]:
    raw = retrying(lambda: http.request(url, limit=MAX_ARCHIVE_BYTES, timeout=120))
    if digest is not None and (not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(raw).hexdigest() != digest):
        raise JobError("archive_digest_mismatch")
    return read_project_zip(raw)


def pack_files(files: tuple[ProjectFile, ...]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in validate_files(files):
            info = zipfile.ZipInfo(file.path, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | (0o755 if file.executable else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, file.data)
            if buffer.tell() > MAX_ARCHIVE_BYTES:
                raise JobError("artifact_too_large")
    data = buffer.getvalue()
    if len(data) > MAX_ARCHIVE_BYTES:
        raise JobError("artifact_too_large")
    return data


def pack_results(output: Path) -> bytes:
    # Called only on the trusted simulator output directory. Never traverse a
    # participant workspace or include a scenario, credentials, or host files.
    files = []
    total = 0
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            raise JobError("artifact_link_rejected")
        if not path.is_file():
            continue
        total += path.stat().st_size
        if total > MAX_EXPANDED_BYTES:
            raise JobError("artifact_too_large")
        files.append(ProjectFile(path.relative_to(output).as_posix(), path.read_bytes()))
    return pack_files(tuple(files))


def upload_artifact(http: Http, descriptor: dict, data: bytes) -> str:
    """An exact one-object signed PUT destination, issued by the trusted backend.

    No administrative key, arbitrary headers, redirect or participant-chosen path
    is accepted. Durable repository archival is performed by the backend after
    this staging transfer, before the phase is enabled.
    """
    if not isinstance(descriptor, dict) or set(descriptor) != {"url", "path"}:
        raise JobError("invalid_artifact_destination")
    path = descriptor["path"]
    if not isinstance(path, str) or not re.fullmatch(r"[0-9a-f-]{36}/[0-9a-f-]{36}/(?:result|preview)\.zip", path):
        raise JobError("invalid_artifact_destination")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise JobError("artifact_too_large")
    http.request(descriptor["url"], data=data, method="PUT", headers={"Content-Type": "application/zip"}, limit=65536)
    return path


def store_private_artifact(files: tuple[ProjectFile, ...], identifier: str, kind: str,
                           credentials: Callable[[], dict] | None) -> str:
    if credentials is None or kind not in ('prepared', 'results'):
        raise JobError('artifact_repository_unavailable')
    value = credentials()
    if (not isinstance(value, dict) or value.get('artifact_id') != identifier or
            value.get('kind') != ('prepare' if kind == 'prepared' else 'engine')):
        raise JobError('artifact_repository_mismatch')
    repository = SnapshotRepository(value['full_name'], value['token'])
    commit, _ = repository.store_snapshot(kind, identifier, files)
    return 'github:' + value['full_name'] + '@' + commit
