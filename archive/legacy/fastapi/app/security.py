"""Password hashing, signed sessions, CSRF, and simple rate limiting (stdlib + itsdangerous)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from typing import Optional

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import get_settings

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt=salt)


def sign_session(user_id: int) -> str:
    return _serializer("session").dumps({"uid": user_id, "n": secrets.token_hex(4)})


def read_session(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        data = _serializer("session").loads(value, max_age=get_settings().session_days * 86400)
    except BadSignature:
        return None
    return int(data.get("uid")) if isinstance(data, dict) and "uid" in data else None


def csrf_token_for(session_value: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, ("csrf:" + (session_value or "anon")).encode(), hashlib.sha256).hexdigest()[:32]


def verify_csrf(session_value: str, token: Optional[str]) -> bool:
    if not token:
        return False
    return hmac.compare_digest(csrf_token_for(session_value), token)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(EMAIL_RE.match(email))


def password_problems(pw: str) -> Optional[str]:
    if len(pw) < 8:
        return "password_too_short"
    if len(pw) > 200:
        return "password_too_long"
    return None


class RateLimiter:
    """Fixed-window in-memory limiter keyed by arbitrary string."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


login_limiter = RateLimiter(limit=10, window_seconds=300)
register_limiter = RateLimiter(limit=20, window_seconds=3600)


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", text.strip().lower()).strip("-")
    return text[:60] or secrets.token_hex(4)
