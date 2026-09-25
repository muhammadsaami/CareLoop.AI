"""
CareLoop AI — Security Foundation (Phase 1 Stub)

This module defines clean interfaces for:
  - password hashing
  - JWT token creation & validation
  - current-user dependency

No real authentication is implemented in Phase 1.
These stubs allow Phase 1.5 / Phase 2 to add authentication without
restructuring the codebase.

IMPORTANT:
  - Do NOT add hardcoded credentials here.
  - Do NOT use admin/admin or password123.
  - JWT_SECRET must come from environment (SECRET_KEY setting).
"""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Password hashing ─────────────────────────────────────────────────────────

def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password.

    Phase 2 implementation: replace with passlib bcrypt.
    Example:
        from passlib.context import CryptContext
        _pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return _pwd_context.hash(plain_password)
    """
    raise NotImplementedError(
        "Password hashing is not implemented in Phase 1. "
        "Add passlib[bcrypt] in Phase 2."
    )


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against a stored hash.

    Phase 2 implementation: replace with passlib.verify.
    """
    raise NotImplementedError(
        "Password verification is not implemented in Phase 1."
    )


# ── JWT ──────────────────────────────────────────────────────────────────────

def create_access_token(subject: str) -> str:
    """
    Create a signed JWT access token.

    Phase 2 implementation: replace with python-jose or PyJWT.
    Example:
        from jose import jwt
        payload = {"sub": subject, "exp": ...}
        return jwt.encode(payload, settings.secret_key, algorithm="HS256")
    """
    raise NotImplementedError(
        "JWT creation is not implemented in Phase 1."
    )


def decode_access_token(token: str) -> dict:
    """
    Decode and validate a JWT access token.

    Phase 2 implementation: validate signature, expiry, and claims.
    """
    raise NotImplementedError(
        "JWT decoding is not implemented in Phase 1."
    )


# ── FastAPI dependency stub ───────────────────────────────────────────────────

def get_current_user():
    """
    FastAPI dependency for the authenticated user.

    Phase 2 implementation:
        async def get_current_user(token: str = Depends(oauth2_scheme), ...):
            ...
    """
    raise NotImplementedError(
        "Authentication dependency is not implemented in Phase 1."
    )
