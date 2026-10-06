"""Constrain model-generated changes to a separately reviewable adapter."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .manifest import MANIFEST_NAME, ProjectError, ProjectManifest, relative_path
from .package import ProjectFile, project_digest, validate_files

ADAPTER_PREFIX = ".observer-adapter/"
MAX_ADAPTER_BYTES = 128 * 1024


@dataclass(frozen=True)
class AdapterProposal:
    source_digest: str
    manifest: ProjectManifest
    files: tuple[ProjectFile, ...]
    explanation: str

    @classmethod
    def parse(cls, response: Mapping[str, Any], source: tuple[ProjectFile, ...]) -> "AdapterProposal":
        if not isinstance(response, Mapping) or set(response) != {"manifest", "files", "explanation"}:
            raise ProjectError("Adapter response must contain manifest, files and explanation.")
        manifest = ProjectManifest.parse(response["manifest"])
        proposed = response["files"]
        if not isinstance(proposed, list) or len(proposed) > 12:
            raise ProjectError("Adapter must contain at most 12 files.")
        originals = {f.path.casefold() for f in source}
        added = []
        total = 0
        for entry in proposed:
            if not isinstance(entry, dict) or set(entry) != {"path", "content"}:
                raise ProjectError("Each adapter file needs a path and text content.")
            path = relative_path(entry["path"])
            if not path.startswith(ADAPTER_PREFIX) or path.casefold() in originals:
                raise ProjectError("Adapters may only add files under .observer-adapter; participant code cannot be replaced.")
            content = entry["content"]
            if not isinstance(content, str):
                raise ProjectError("Adapter content must be text.")
            encoded = content.encode()
            total += len(encoded)
            if total > MAX_ADAPTER_BYTES:
                raise ProjectError("Adapter code exceeds the size limit.")
            added.append(ProjectFile(path, encoded))
        if len({f.path.casefold() for f in added}) != len(added):
            raise ProjectError("Adapter contains duplicate paths.")
        explanation = response["explanation"]
        if not isinstance(explanation, str) or len(explanation) > 8000:
            raise ProjectError("Adapter explanation must be short text.")
        # Detect prefix conflicts with original directories/files as well.
        validate_files((*source, *added))
        return cls(project_digest(source), manifest, tuple(sorted(added, key=lambda f: f.path)), explanation)

    @property
    def digest(self) -> str:
        document = {"source": self.source_digest, "manifest": self.manifest.as_dict(),
                    "files": [{"path": f.path, "content": f.data.decode()} for f in self.files]}
        return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def materialize(self, source: tuple[ProjectFile, ...], *, confirmed_digest: str) -> tuple[ProjectFile, ...]:
        if project_digest(source) != self.source_digest or confirmed_digest != self.digest:
            raise ProjectError("The project or adapter changed. Review and confirm the new version.")
        # Keep the original manifest in the source revision; the separately approved
        # revision receives the reviewed execution manifest.
        files = tuple(f for f in source if f.path != MANIFEST_NAME)
        return validate_files((*files, *self.files, ProjectFile(MANIFEST_NAME, self.manifest.canonical_bytes())))
