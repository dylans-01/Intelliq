"""Password hashing and server-side sessions.

Sessions are opaque random tokens stored only as SHA-256 hashes; the raw
token lives in an HttpOnly, SameSite=Lax cookie. Each session carries a CSRF
token that state-changing requests must echo in the ``X-Intelliq-CSRF``
header (double-submit), so a cross-site form post cannot act as the user.

The identity provider is pluggable: this module is the built-in local
provider. SSO (OIDC/SAML) would create sessions through the same store API.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .store import Store

_hasher = PasswordHasher()
# Verified against when the e-mail is unknown so response time does not reveal account existence.
_DUMMY_HASH = _hasher.hash("intelliq-timing-equaliser")

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(store: Store, user_id: str, hours: int, ip: str | None, user_agent: str | None) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = (datetime.now(UTC) + timedelta(hours=hours)).isoformat(timespec="seconds")
    store.create_session(token_hash(token), user_id, csrf, expires, ip, user_agent)
    return token, csrf


def csrf_matches(expected: str, supplied: str | None) -> bool:
    return bool(supplied) and hmac.compare_digest(expected, supplied or "")
