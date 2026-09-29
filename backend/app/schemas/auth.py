"""
CareLoop AI — Authentication Schemas (Phase 8A)

Self-service registration and login.  A registration response returns a signed
session immediately, so a newly registered patient is usable without a separate
login round trip.

Validation notes
----------------
* Emails are normalized to a stripped, lowercased value BEFORE uniqueness is
  checked, matching `app/cli/manage_access.py` and the column-level comment on
  `app_users.email`.  Two spellings of the same mailbox must lock their owner
  out, not create two accounts.
* Password policy is enforced here (422) AND re-checked by `hash_password`
  (ValueError).  The schema is the gate for HTTP so a bad value is a clean 422
  instead of an unhandled 500; the primitive stays the authority.
"""
from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import BCRYPT_MAX_PASSWORD_BYTES, MIN_PASSWORD_LENGTH

#: RFC 5321 maximum length of a forward-path email address, matching the column.
EMAIL_MAX_LENGTH = 320


def normalize_email(value: str) -> str:
    """Canonical form for storage and comparison."""
    return value.strip().lower()


class RegisterRequest(BaseModel):
    """Payload for `POST /api/v1/auth/register`."""

    full_name: str = Field(
        ..., min_length=1, max_length=255, description="Patient's full name"
    )
    email: EmailStr = Field(..., max_length=EMAIL_MAX_LENGTH)
    password: str = Field(..., min_length=1)

    @field_validator("full_name", mode="before")
    @classmethod
    def _strip_full_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_email(value)
        return value

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        # Mirrors `app.core.security._validate_password`, so a request is
        # refused here with a pydantic 422 rather than leaving the rejection to
        # a 500 or a CLI message.
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
            raise ValueError(
                "Password must be at most 72 bytes when UTF-8 encoded."
            )
        return value


class LoginRequest(BaseModel):
    """Payload for `POST /api/v1/auth/login`."""

    # No password-strength policy is applied on login: whatever was used at
    # registration is the credential to match, and rejecting a stored password
    # here would lock out an account instead of hardening it.
    email: EmailStr = Field(..., max_length=EMAIL_MAX_LENGTH)
    password: str = Field(..., min_length=1)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_email(value)
        return value


class UserResponse(BaseModel):
    """A caller-visible account, without any secret or clinical content."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    #: The patient record this account holds a `self` grant to, when it has
    #: one.  Resolved from `patient_access`, never from a caller-supplied id.
    patient_id: Optional[uuid.UUID] = None


class AuthSessionResponse(BaseModel):
    """The response to a successful registration or login."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserResponse
    patient_id: Optional[uuid.UUID] = None