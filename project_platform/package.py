"""Validate complete project archives without executing participant content."""
from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .manifest import MANIFEST_NAME, ProjectError, relative_path

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_FILES = 10_000
_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}


@dataclass(frozen=True)
class ProjectFile:
    path: str
    data: bytes
    executable: bool = False


def _validate_source_path(path: str) -> str:
    path = relative_path(path)
    parts = path.split("/")
    if any(part.casefold() == ".git" for part in parts):
        raise ProjectError("Do not include .git history in a project ZIP.")
    for part in parts:
        if (part == ".env" or part.startswith(".env.")) and part not in _ENV_TEMPLATES:
            raise ProjectError("Remove .env credentials and enter API keys separately on the website.")
    return path


def read_project_zip(data: bytes) -> tuple[ProjectFile, ...]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ProjectError("Project ZIP exceeds the upload size limit.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES:
                raise ProjectError("Project contains too many archive entries.")
            total = 0
            paths: set[str] = set()
            items: list[ProjectFile] = []
            for info in infos:
                path = _validate_source_path(info.filename.rstrip("/"))
                mode = info.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ProjectError("Project ZIP cannot contain links or special files.")
                if info.flag_bits & 1:
                    raise ProjectError("Encrypted ZIP entries are not supported.")
                if info.is_dir():
                    continue
                if file_type == stat.S_IFDIR:
                    raise ProjectError("Project ZIP has an inconsistent directory entry.")
                folded = path.casefold()
                if folded in paths:
                    raise ProjectError("Project ZIP contains duplicate or case-colliding paths.")
                paths.add(folded)
                total += info.file_size
                if total > MAX_EXPANDED_BYTES:
                    raise ProjectError("Expanded project exceeds the size limit.")
                with archive.open(info) as handle:
                    content = handle.read(min(info.file_size, MAX_EXPANDED_BYTES) + 1)
                if len(content) != info.file_size:
                    raise ProjectError("Project ZIP contains an invalid file size.")
                items.append(ProjectFile(path, content, bool(mode & 0o111)))
    except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError) as exc:
        raise ProjectError("Project ZIP is invalid or uses unsupported compression.") from exc
    if not items:
        raise ProjectError("Project ZIP contains no files.")
    # GitHub-generated archives and folder uploads commonly have one outer directory.
    roots = {item.path.split("/")[0] for item in items}
    if len(roots) == 1 and all("/" in item.path for item in items):
        items = [ProjectFile(item.path.split("/", 1)[1], item.data, item.executable) for item in items]
    return validate_files(items)


def validate_files(files: Iterable[ProjectFile]) -> tuple[ProjectFile, ...]:
    items = tuple(sorted(files, key=lambda item: item.path))
    if not items or len(items) > MAX_FILES or sum(len(f.data) for f in items) > MAX_EXPANDED_BYTES:
        raise ProjectError("Project is empty or exceeds the file limits.")
    seen: set[str] = set()
    for item in items:
        _validate_source_path(item.path)
        if item.path.casefold() in seen:
            raise ProjectError("Project contains colliding paths.")
        seen.add(item.path.casefold())
    for path in seen:
        parts = path.split("/")
        if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
            raise ProjectError("A project file conflicts with a directory path.")
    return items


def project_digest(files: Iterable[ProjectFile]) -> str:
    digest = hashlib.sha256()
    for item in validate_files(files):
        # Length-prefix every field so neither file boundaries nor modes are ambiguous.
        for field in (item.path.encode(), b"x" if item.executable else b"-", item.data):
            digest.update(len(field).to_bytes(8, "big"))
            digest.update(field)
    return digest.hexdigest()


def extract_project(files: Iterable[ProjectFile], destination: Path) -> None:
    items = validate_files(files)
    # Always use a fresh workspace; never follow a pre-existing symlink or overwrite a checkout.
    destination.mkdir(parents=True, exist_ok=False)
    for item in items:
        target = destination / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.data)
        target.chmod(0o755 if item.executable else 0o644)


def read_manifest(files: Iterable[ProjectFile]):
    from .manifest import ProjectManifest
    item = next((f for f in files if f.path == MANIFEST_NAME), None)
    return ProjectManifest.parse(item.data) if item else None
