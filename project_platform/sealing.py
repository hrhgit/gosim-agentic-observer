"""Public-key sealing for the public-repository runner pool ("observer-seal-v1").

A public repository's logs, artifacts and dispatch inputs are visible to anyone,
so every private byte a public-pool job receives or returns is sealed:

* inputs (the claim payload, the scenario bundle and the participant project)
  are sealed by the backend to a per-job X25519 key that the runner generates
  in memory and announces with its claim; the private half never leaves the
  process;
* the result is sealed to the backend's X25519 public key; only the backend
  holds the private key (a Supabase secret, never in a repository).

Format: b"OSB1" || ephemeral public key (32) || AES-256-GCM ciphertext and tag.
Key and nonce come from HKDF-SHA256 over the X25519 shared secret, salted with
both public keys; the context string (e.g. "result:<job id>") is bound as HKDF
info and as AEAD associated data, so a sealed object cannot be replayed as a
different object or for a different job. The backend implementation is
supabase/functions/_shared/observer-seal.ts.

The `cryptography` package is imported lazily: only the public pool installs
it (hash-pinned), the private runtime never needs it.
"""
from __future__ import annotations

import base64
import binascii
import re

from .job_client import JobError

MAGIC = b"OSB1"
INFO = b"observer-seal-v1:"
OVERHEAD = len(MAGIC) + 32 + 16
_KEY = re.compile(r"[A-Za-z0-9_-]{43}")


def encode_key(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_key(value: object) -> bytes:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise JobError("invalid_seal_key")
    try:
        raw = base64.urlsafe_b64decode(value + "=")
    except (binascii.Error, ValueError):
        raise JobError("invalid_seal_key") from None
    if len(raw) != 32:
        raise JobError("invalid_seal_key")
    return raw


def _derive(shared: bytes, ephemeral: bytes, recipient: bytes, context: str) -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    if shared == bytes(32):
        raise JobError("invalid_seal_key")
    material = HKDF(algorithm=hashes.SHA256(), length=44, salt=ephemeral + recipient,
                    info=INFO + context.encode()).derive(shared)
    return material[:32], material[32:]


def seal(recipient: bytes, data: bytes, context: str) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    ephemeral = X25519PrivateKey.generate()
    public = ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    try:
        shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient))
    except ValueError:
        raise JobError("invalid_seal_key") from None
    key, nonce = _derive(shared, public, recipient, context)
    return MAGIC + public + AESGCM(key).encrypt(nonce, data, context.encode())


class SealKey:
    """A per-job X25519 key pair; the private half exists only in this process."""

    def __init__(self, private: bytes | None = None):
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        # A fixed private key is for the cross-implementation tests only.
        self._private = X25519PrivateKey.generate() if private is None else X25519PrivateKey.from_private_bytes(private)
        self.public = self._private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def public_text(self) -> str:
        return encode_key(self.public)

    def open(self, sealed: bytes, context: str) -> bytes:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(sealed) < OVERHEAD or sealed[:4] != MAGIC:
            raise JobError("invalid_sealed_input")
        ephemeral = sealed[4:36]
        try:
            shared = self._private.exchange(X25519PublicKey.from_public_bytes(ephemeral))
        except ValueError:
            raise JobError("invalid_sealed_input") from None
        key, nonce = _derive(shared, ephemeral, self.public, context)
        try:
            return AESGCM(key).decrypt(nonce, sealed[36:], context.encode())
        except InvalidTag:
            raise JobError("invalid_sealed_input") from None
