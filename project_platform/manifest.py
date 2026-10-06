"""Language-neutral, serializable execution contracts; never execute on import."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

MANIFEST_NAME = "observer.project.json"
SCHEMA = "observer-project-v1"
MAX_MANIFEST_BYTES = 64 * 1024
_IMAGE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:-]*(?:@sha256:[0-9a-f]{64})?$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_PRIVATE_ENV = re.compile(r"(SECRET|PASSWORD|TOKEN|API_KEY|PRIVATE_KEY)")


class ProjectError(ValueError):
    """A participant-facing, non-secret validation error."""


def relative_path(value: Any, *, allow_dot: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ProjectError("Project paths must be nonempty relative paths.")
    if value == "." and allow_dot:
        return value
    parts = value.split("/")
    if (value.startswith("/") or "\\" in value or ":" in value or
            any(p in ("", ".", "..") for p in parts) or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ProjectError("Project paths cannot be absolute or traverse directories.")
    return str(PurePosixPath(value))


def _argv(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        raise ProjectError(f"{field} must be a nonempty array of command arguments.")
    if any(not isinstance(v, str) or not v or "\x00" in v or len(v) > 8192 for v in value):
        raise ProjectError(f"{field} contains an invalid command argument.")
    if value[0].startswith("-"):
        raise ProjectError(f"{field} must start with an executable.")
    return tuple(value)


@dataclass(frozen=True)
class ProjectManifest:
    image: str
    run: tuple[str, ...]
    build: tuple[tuple[str, ...], ...] = ()
    working_directory: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    protocol: str = "jsonl-v2"

    @classmethod
    def parse(cls, raw: str | bytes | Mapping[str, Any]) -> "ProjectManifest":
        if isinstance(raw, (str, bytes)):
            if len(raw.encode() if isinstance(raw, str) else raw) > MAX_MANIFEST_BYTES:
                raise ProjectError("Project manifest is too large.")
            try:
                raw = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ProjectError("Project manifest is not valid JSON.") from exc
        if not isinstance(raw, Mapping):
            raise ProjectError("Project manifest must be a JSON object.")
        allowed = {"schema_version", "image", "run", "build", "working_directory", "environment", "protocol"}
        if set(raw) - allowed:
            raise ProjectError("Unknown project manifest fields: " + ", ".join(sorted(set(raw) - allowed)))
        if raw.get("schema_version") != SCHEMA:
            raise ProjectError(f"Project schema_version must be {SCHEMA}.")
        image = raw.get("image")
        if not isinstance(image, str) or len(image) > 256 or not _IMAGE.fullmatch(image):
            raise ProjectError("image must be a container image reference.")
        # "jsonl-v4" names the same JSON-Lines transport; the gameplay version is chosen by
        # the scenario and announced in each message's protocol_version. Normalized, so
        # manifest digests and approvals are unchanged.
        if raw.get("protocol", "jsonl-v2") not in ("jsonl-v2", "jsonl-v4"):
            raise ProjectError("Projects must expose the jsonl-v2 (or jsonl-v4) interface, directly or through an adapter.")
        build = raw.get("build", [])
        if not isinstance(build, list) or len(build) > 16:
            raise ProjectError("build must contain at most 16 commands.")
        env = raw.get("environment", {})
        if not isinstance(env, Mapping) or len(env) > 32:
            raise ProjectError("environment must be an object with at most 32 settings.")
        for key, value in env.items():
            if (not isinstance(key, str) or not _ENV.fullmatch(key) or
                    key.startswith(("GITHUB_", "SUPABASE_", "OBSERVER_", "ACTIONS_")) or _PRIVATE_ENV.search(key)):
                raise ProjectError("Credentials and platform settings must not be placed in the project manifest.")
            if not isinstance(value, str) or "\x00" in value or len(value) > 4096:
                raise ProjectError("Environment settings must be short strings.")
        return cls(
            image=image, run=_argv(raw.get("run"), "run"),
            build=tuple(_argv(cmd, "build") for cmd in build),
            working_directory=relative_path(raw.get("working_directory", "."), allow_dot=True),
            environment=tuple(sorted(env.items())),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA, "protocol": self.protocol, "image": self.image,
            "run": list(self.run), "build": [list(cmd) for cmd in self.build],
            "working_directory": self.working_directory, "environment": dict(self.environment),
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()
