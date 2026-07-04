"""Security primitives: password hashing (bcrypt) and JWT issue/verify (PyJWT).

Kept in one leaf module so crypto choices live in exactly one place.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from jwt import InvalidTokenError

from ..config import settings


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt (salt embedded in the output).

    WHY bcrypt: industry-standard adaptive hash. The salt is part of the returned
    string, so we store a single column and never manage salts manually.
    """
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True iff `plain` matches `hashed`. Constant-time compare inside bcrypt."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed/unsupported hash — fail closed rather than raise.
        return False


def create_access_token(subject: str | int, role: str, extra: dict[str, Any] | None = None) -> str:
    """Issue a signed JWT carrying the user id, role, and expiry."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),  # 'sub' = standard JWT claim for the principal (user id)
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify signature + expiry. Raises jwt.InvalidTokenError on any failure."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
