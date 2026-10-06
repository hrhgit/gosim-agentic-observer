"""Prepare a reviewable project; never run its commands on this trusted host."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import replace

from .adaptation import AdapterProposal
from .artifacts import download_project, pack_files, store_private_artifact, upload_artifact
from .job_client import Http, JobError
from .model_adapter import propose_adapter
from .manifest import ProjectError
from .model_client import ModelClient, team_model_client
from .package import project_digest, read_manifest
from .repository import SnapshotRepository
from .team_egress import checked_team_egress


def resolve_image(image: str) -> str:
    """Resolve a public registry image without running its entrypoint or commands."""
    # Restrict registry routing, not language. Images cannot redirect the Docker
    # daemon toward an organizer's internal registry or supply credentials.
    first = image.split("/", 1)[0]
    if "/" in image and ("." in first or ":" in first or first == "localhost"):
        if first not in ("docker.io", "ghcr.io", "mcr.microsoft.com", "quay.io"):
            raise JobError("container_registry_not_supported")
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
           if key in os.environ}
    try:
        pulled = subprocess.run(["docker", "pull", "--quiet", image], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        if pulled.returncode:
            raise JobError("container_image_unavailable")
        inspected = subprocess.run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image],
                                   env=env, capture_output=True, timeout=15)
        if inspected.returncode or len(inspected.stdout) > 65536:
            raise JobError("container_image_unavailable")
        digests = json.loads(inspected.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise JobError("container_image_unavailable") from None
    if not isinstance(digests, list):
        raise JobError("container_image_unavailable")
    if "@sha256:" in image:
        # Docker pull already checked the requested content-addressed image.
        return image
    # Images can have multiple repository tags locally. Bind to the requested
    # repository, not whichever unrelated alias Docker happens to print first.
    repository = image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image
    def normalized(value):
        value = value.removeprefix("docker.io/")
        return value.removeprefix("library/") if value.count("/") == 1 else value
    for digest in digests:
        if (isinstance(digest, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:-]*@sha256:[0-9a-f]{64}", digest)
                and normalized(digest.split("@", 1)[0]) == normalized(repository)):
            return digest
    raise JobError("container_image_unavailable")


def prepare_project(payload: dict, http: Http, *, repository_credentials=None) -> dict:
    source = download_project(http, payload["archive_url"], payload.get("source_digest"))
    repository = payload["repository"]
    store = SnapshotRepository(repository["full_name"], repository["token"])
    commit, source_hash = store.store_revision(payload["revision_id"], source)
    manifest = read_manifest(source)
    if manifest is not None:
        proposal = AdapterProposal(source_hash, manifest, (), "Project supplies its own JSON-Lines interface.")
    else:
        if "team_egress" in payload:
            # Direct model access: the team's own provider from its variables, as in an
            # evaluation; the platform model proxy is not involved.
            client, model = team_model_client(checked_team_egress(payload["team_egress"]))
        elif all(payload.get(key) for key in ("model", "model_base_url", "run_credential")):
            client, model = ModelClient(payload["model_base_url"], payload["run_credential"]), payload["model"]
        else:
            raise JobError("project_interface_required")
        # The public-test scenario decides the protocol the adapter must speak.
        gameplay = payload.get("gameplay", "v3")
        if gameplay not in ("v3", "v4"):
            raise JobError("invalid_job_payload")
        used = getattr(client, "source", "")
        try:
            proposal = propose_adapter(source, model, client, gameplay=gameplay)
        except ProjectError as error:
            if not used:
                raise
            raise ProjectError("Automatic adaptation used " + used + ": " + str(error)) from None
        if used:
            proposal = replace(proposal, explanation=(proposal.explanation + "\n\nAutomatic adaptation used "
                                                      + used + ".").strip())
    proposal = replace(proposal, manifest=replace(proposal.manifest, image=resolve_image(proposal.manifest.image)))
    # Materialization here is only for the public preview. It does not imply
    # participant approval and cannot enqueue a formal evaluation.
    materialized = proposal.materialize(source, confirmed_digest=proposal.digest)
    if payload['artifact_upload'] == {'kind': 'github'}:
        path = store_private_artifact(materialized, payload['revision_id'], 'prepared', repository_credentials)
    else:
        path = upload_artifact(http, payload["artifact_upload"], pack_files(materialized))
    return {
        "revision_id": payload["revision_id"], "source_digest": source_hash,
        "source_commit": commit, "repository": repository["full_name"],
        "manifest": proposal.manifest.as_dict(),
        "adapter_files": {item.path: item.data.decode() for item in proposal.files},
        "approval_digest": proposal.digest, "explanation": proposal.explanation,
        "materialized_digest": project_digest(materialized), "preview_path": path,
        "status": "awaiting_public_test",
    }
