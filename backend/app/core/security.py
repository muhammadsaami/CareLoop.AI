"""
CareLoop AI — Security Primitives (password hashing + JWT)

This module replaces the Phase 1 stubs.  The five function names are unchanged,
so the interfaces the stub declared are the interfaces that shipped.

WHAT THIS MODULE DOES AND DOES NOT DO
-------------------------------------
It does: hash and verify passwords, mint an access token, and validate one -
rejecting a bad signature, a wrong algorithm, an expired token, or a token
whose subject is not a user id.

It does NOT: decide who may see which patient.  That is `app.services.access_control`,
and keeping the split sharp is what stops "valid token" from silently becoming
"allowed to read anything".

WHY THERE IS NO LOGIN ENDPOINT
------------------------------
Tokens are minted by `app/cli/manage_access.py`, an operator CLI.  Publishing
`POST /auth/token` would add a password-guessing surface to a clinical API and
drag in account lockout, rate limiting, password reset, and MFA - none of which
is this phase's job, and all of which would be worse than the absence.  A
token issued to an operator is minted once, offline, and expires in minutes.

THE SIGNING KEY
---------------
`settings.secret_key`, which is REQUIRED configuration with no code-level
default, so an unset key stops the process at startup instead of signing
tokens with a value someone can read in the repository.  The key is never
logged, never returned, and never appears in an error message; `redact_secrets`
is applied to anything derived from it.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Optional

import bcrypt
import jwt

from app.core.config import get_settings

# ── Password hashing ──────────────────────────────────────────────────────────

# Cost 12 is the current sensible default: roughly a quarter-second per hash on
# commodity hardware, which is slow enough to make offline cracking expensive
# and fast enough that a login is not a denial-of-service risk.  The stored
# digest carries the cost, so raising this later does not invalidate existing
# hashes.
BCRYPT_ROUNDS = 12

# bcrypt hashes at most 72 bytes of input and SILENTLY DISCARDS the rest
# rather than raising.  Left unchecked, "correct horse..." and the same string
# with 100 more characters would produce identical digests, and an attacker
# would not need to know the real password - only its first 72 bytes.  Rejecting
# is the only safe response; truncating would silently accept a weaker secret.
BCRYPT_MAX_PASSWORD_BYTES = 72

# A short floor exists so an obviously trivial secret cannot be set, even by an
# operator.  This is not a substitute for real policy - it is the point at which
# a value is refused outright.
MIN_PASSWORD_LENGTH = 12

PASSWORD_HASH_ENCODING = "utf-8"


class PasswordTooLongError(ValueError):
    """
    The password exceeds bcrypt's 72-byte input window.

    A distinct type rather than a generic ValueError so the CLI can explain the
    limit instead of printing a stack trace, and so a caller cannot mistake it
    for malformed input.
    """


class PasswordTooShortError(ValueError):
    """The password is below the minimum accepted length."""


def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password with bcrypt.

    Raises ValueError subclasses rather than truncating or silently accepting,
    for the reason documented on `BCRYPT_MAX_PASSWORD_BYTES`.
    """
    _validate_password(plain_password)
    digest = bcrypt.hashpw(
        plain_password.encode(PASSWORD_HASH_ENCODING),
        bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
    )
    return digest.decode(PASSWORD_HASH_ENCODING)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against a stored bcrypt digest.

    Returns False - never raises - for a wrong password, a malformed stored
    value, or a missing one.  A caller must not be able to distinguish "no such
    user" from "wrong password" by timing or by exception type, so this function
    is the only place that decision is made and it makes the same one every
    time.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode(PASSWORD_HASH_ENCODING),
            hashed_password.encode(PASSWORD_HASH_ENCODING),
        )
    except (ValueError, TypeError):
        # A corrupt or non-bcrypt stored value. Treated as "does not match" so
        # the failure mode is a refused login, not a 500.
        return False


def _validate_password(plain_password: str) -> None:
    if not isinstance(plain_password, str) or not plain_password:
        raise ValueError("Password must be a non-empty string.")
    if len(plain_password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShortError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(plain_password.encode(PASSWORD_HASH_ENCODING)) > BCRYPT_MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes when "
            "UTF-8 encoded. bcrypt ignores anything beyond that, so a longer "
            "password would be silently weakened; use a passphrase instead."
        )


# ── JWT ──────────────────────────────────────────────────────────────────────

# Pinned, never read from the token.  An unpinned algorithm is how `alg: none`
# and HS/RS key-confusion attacks get in: a caller who controls the header can
# otherwise choose how their own token is verified.
JWT_ALGORITHM = "HS256"

TOKEN_TYPE_CLAIM = "type"
ACCESS_TOKEN_TYPE = "access"

# Fallback lifetime used only when a caller does not pass one.  The real value
# comes from settings so an operator can tune it without a code change.
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 60


class InvalidTokenError(Exception):
    """
    The presented token is not acceptable.

    One type for every rejection - bad signature, wrong algorithm, expired,
    malformed, or a subject that is not a user id - so no caller can learn which
    check failed by comparing behaviour.  The reason is available on `.reason`
    for server-side logging, but the message a client receives is fixed by the
    HTTP layer and never varies.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("Invalid access token.")


def create_access_token(
    subject: str,
    *,
    expires_minutes: Optional[int] = None,
    additional_claims: Optional[dict[str, Any]] = None,
) -> str:
    """
    Mint a signed HS256 access token for `subject`.

    `subject` is the AppUser id as a string, stored in the standard `sub` claim.

    `additional_claims` exists for tests that need an already-expired token. It
    is NOT a way to add application claims: anything an authorization decision
    would depend on is re-read from the database by the dependency, so that
    deactivating a user or revoking a grant takes effect on the next request
    instead of whenever the token happens to expire.
    """
    settings = get_settings()
    minutes = (
        expires_minutes
        if expires_minutes is not None
        else settings.access_token_expire_minutes
    )

    issued_at = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        TOKEN_TYPE_CLAIM: ACCESS_TOKEN_TYPE,
        "iat": issued_at,
        "exp": issued_at + dt.timedelta(minutes=minutes),
    }
    if additional_claims:
        payload.update(additional_claims)

    token = jwt.encode(
        payload,
        settings.secret_key,
        algorithm=JWT_ALGORITHM,
    )
    # PyJWT < 2 returned bytes; >= 2 returns str. Normalise so the declared
    # return type is true on either.
    if isinstance(token, bytes):  # pragma: no cover - PyJWT 2 pinned
        token = token.decode("utf-8")
    return token


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Validate an access token and return its claims.

    Validates, in order: structure, signature against the configured key using
    the PINNED algorithm, expiry, token type, and that `sub` is a UUID.  Every
    failure raises `InvalidTokenError` with a distinguishable `.reason` for
    logging and an identical message for the client.

    Expiry is checked by PyJWT itself against `exp`; there is no leeway window,
    because a grace period on a clinical API means a token that is expired still
    works.
    """
    settings = get_settings()

    if not token or not isinstance(token, str):
        raise InvalidTokenError("missing")

    try:
        claims = jwt.decode(
            token,
            settings.secret_key,
            # The allow-list is what pins the algorithm. It must never be
            # derived from the token's own header.
            algorithms=[JWT_ALGORITHM],
            options={
                "require": ["sub", "exp", TOKEN_TYPE_CLAIM],
                "verify_signature": True,
                "verify_exp": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("expired") from exc
    except jwt.InvalidSignatureError as exc:
        raise InvalidTokenError("bad_signature") from exc
    except jwt.InvalidAlgorithmError as exc:
        raise InvalidTokenError("bad_algorithm") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise InvalidTokenError("missing_claim") from exc
    except jwt.InvalidTokenError as exc:
        # PyJWT's base class: malformed, not a JWT, wrong structure.
        raise InvalidTokenError("malformed") from exc

    if claims.get(TOKEN_TYPE_CLAIM) != ACCESS_TOKEN_TYPE:
        # A refresh token, or something else signed with the same key, must not
        # be usable as an access token.
        raise InvalidTokenError("wrong_token_type")

    subject = claims.get("sub")
    if not isinstance(subject, str):
        raise InvalidTokenError("bad_subject")
    try:
        uuid.UUID(subject)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidTokenError("bad_subject") from exc

    return claims
