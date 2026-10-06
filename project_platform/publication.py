"""Lossless, bounded transport for large public catalogs; no hidden inputs."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json

FORMAT = "observer-publication-gzip-v1"
MAX_PUBLICATION_BYTES = 96 * 1024 * 1024
MAX_WIRE_BYTES = 15 * 1024 * 1024


def encode_publication(publication: dict) -> dict:
    raw = json.dumps(publication, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > MAX_PUBLICATION_BYTES:
        raise ValueError("public_catalog_too_large")
    if len(raw) <= 1024 * 1024:
        return publication
    encoded = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")
    if len(encoded) > MAX_WIRE_BYTES:
        raise ValueError("public_catalog_transport_too_large")
    return {"transport_format": FORMAT, "uncompressed_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "data": encoded}


def decode_publication(publication: dict) -> dict:
    if publication.get("transport_format") != FORMAT:
        return publication
    size = publication.get("uncompressed_bytes")
    data = publication.get("data")
    if (type(size) is not int or not 0 < size <= MAX_PUBLICATION_BYTES
            or not isinstance(data, str) or len(data) > MAX_WIRE_BYTES):
        raise ValueError("invalid_public_catalog_transport")
    compressed = base64.b64decode(data, validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(size + 1)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != publication.get("sha256"):
        raise ValueError("public_catalog_integrity_failed")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("invalid_public_catalog")
    return result
