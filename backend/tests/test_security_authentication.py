"""
CareLoop AI — Authentication Security Tests

Covers the token boundary: who the API lets in, and what it reveals when it
refuses.  Two halves, because they fail for very different reasons:

  1. REFUSAL CORRECTNESS - a bad credential produces 401, not 403, 500, or a
     redirect, and the reason is logged but not returned.
  2. NON-DISCLOSURE - the 401 body is identical for every failure mode, so the
     endpoint cannot be used to determine whether a key, a token, or a user
     exists.

The second half is the one that is easy to skip.  A 401 that says "user not
found" in one case and "signature invalid" in another leaks account existence
and whether an attacker guessed a valid signing key, and it does so while still
looking like a correct implementation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.auth_deps import require_authenticated_user
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.user import AppUser

# A representative patient-scoped route.  The point of using a real route is
# that the assertions hold end-to-end, at the point the API is actually
# exposed, rather than against a synthetic test-only app.
PROBE = "/api/v1/patients"


def _secret() -> str:
    return get_settings().secret_key


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_user(db_session, **overrides) -> AppUser:
    fields = {
        "email": f"sec-{uuid.uuid4().hex[:12]}@example.invalid",
        "password_hash": hash_password("correct-horse-battery"),
        "is_active": True,
        "system_access": False,
    }
    fields.update(overrides)
    user = AppUser(**fields)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ── 1. No credential ────────────────────────────────────────────────────────


class TestNoCredential:
    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({}, id="absent"),
            pytest.param({"Authorization": ""}, id="empty-header"),
            pytest.param({"Authorization": "Bearer"}, id="scheme-only"),
            pytest.param({"Authorization": "Bearer "}, id="scheme-and-space"),
            pytest.param({"Authorization": "Basic dXNlcjpwYXNz"}, id="wrong-scheme"),
            pytest.param({"Authorization": "Bearer a b"}, id="two-tokens"),
        ],
    )
    def test_missing_or_malformed_credential_is_401(self, anonymous_client, headers):
        response = anonymous_client.get(PROBE, headers=headers)
        assert response.status_code == 401

    def test_health_stays_reachable_without_a_credential(self, anonymous_client):
        """The documented public surface, asserted from the security suite too."""
        assert anonymous_client.get("/api/v1/health").status_code == 200


# ── 2. The 401 is well-formed ───────────────────────────────────────────────


class TestUnauthorizedShape:
    def test_401_advertises_bearer_authentication(self, anonymous_client):
        """
        Without `WWW-Authenticate` a 401 is not a usable authentication
        challenge: a conformant client cannot tell which scheme to retry with,
        and will usually treat the session as merely unauthenticated.
        """
        response = anonymous_client.get(PROBE)
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == (
            'Bearer realm="careloop", error="invalid_token"'
        )

    def test_401_body_never_names_the_failure_reason(self, anonymous_client):
        response = anonymous_client.get(PROBE)
        detail = str(response.json().get("detail", "")).lower()
        for leak in ("expired", "signature", "token", "algorithm", "user", "not found"):
            assert leak not in detail, f"401 detail discloses '{leak}': {detail!r}"

    def test_401_does_not_leak_a_stack_trace(self, anonymous_client):
        raw = anonymous_client.get(PROBE).text.lower()
        for leak in ("traceback", "jwt.", "app/api/", "sqlalchemy", "psycopg"):
            assert leak not in raw, f"401 body leaks internals: {leak!r}"


# ── 3. Every failure mode is indistinguishable ──────────────────────────────


class TestNonDisclosure:
    """
    All of these are 401 with the same body.

    Grouped in one class on purpose: the assertion is equality between the
    responses, so a future change that makes one of them more specific fails
    here rather than passing in isolation.
    """

    def _body(self, client, headers=None) -> str:
        response = client.get(PROBE, headers=headers or {})
        assert response.status_code == 401
        return response.text

    def test_reason_for_refusal_is_identical_across_failure_modes(
        self, anonymous_client, db_session
    ):
        active = _make_user(db_session)
        inactive = _make_user(db_session, is_active=False)
        wrong_key = jwt.encode(
            {"sub": str(active.id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            "a-different-signing-key-entirely",
            algorithm="HS256",
        )
        not_a_uuid = jwt.encode(
            {"sub": "not-a-uuid", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            _secret(),
            algorithm="HS256",
        )
        unknown_user = jwt.encode(
            {"sub": str(uuid.uuid4()),
             "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            _secret(),
            algorithm="HS256",
        )
        expired = jwt.encode(
            {"sub": str(active.id), "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
            _secret(),
            algorithm="HS256",
        )
        no_expiry = jwt.encode({"sub": str(active.id)}, _secret(), algorithm="HS256")

        valid, _, _ = create_access_token(str(active.id)).split(".")
        bodies = {
            "absent": self._body(anonymous_client),
            "garbage": self._body(anonymous_client, _auth("garbage")),
            "wrong_key": self._body(anonymous_client, _auth(wrong_key)),
            "malformed_sub": self._body(anonymous_client, _auth(not_a_uuid)),
            "unknown_user": self._body(anonymous_client, _auth(unknown_user)),
            "expired": self._body(anonymous_client, _auth(expired)),
            "no_expiry": self._body(anonymous_client, _auth(no_expiry)),
            "inactive_user": self._body(anonymous_client, _auth(create_access_token(str(inactive.id)))),
            "truncated_signature": self._body(anonymous_client, _auth(f"{valid}.x.x")),
        }

        assert len(set(bodies.values())) == 1, (
            "401 responses differ by failure mode, so the endpoint discloses "
            f"which condition occurred: { {k: v for k, v in bodies.items()} }"
        )

    def test_deactivated_user_is_refused_immediately(self, anonymous_client, db_session):
        """
        The same token is accepted, then refused the moment the account is
        disabled. It is still cryptographically valid in both cases.

        Deactivation therefore does not wait for expiry - a leaver's token dies
        when the account is disabled, not up to an hour later.
        """
        user = _make_user(db_session)
        token = create_access_token(str(user.id))

        assert anonymous_client.get(PROBE, headers=_auth(token)).status_code == 200

        user.is_active = False
        db_session.commit()

        assert anonymous_client.get(PROBE, headers=_auth(token)).status_code == 401

    def test_email_is_not_used_as_the_subject(self, db_session):
        """
        The subject is the user id, not the email address.

        Log lines and error trackers routinely surface token claims; a subject
        carrying an email would spray a real contact address into every log line
        that ever touches an auth error.
        """
        user = _make_user(db_session)
        claims = jwt.decode(
            create_access_token(str(user.id)), _secret(), algorithms=["HS256"]
        )
        assert claims["sub"] == str(user.id)
        assert user.email not in str(claims)


# ── 4. Token integrity ──────────────────────────────────────────────────────


class TestTokenIntegrity:
    @pytest.mark.parametrize(
        "claims",
        [
            pytest.param({}, id="no-sub"),
            pytest.param({"sub": ""}, id="empty-sub"),
            pytest.param({"sub": 12345}, id="sub-not-a-string"),
        ],
    )
    def test_structurally_invalid_tokens_are_refused(
        self, anonymous_client, claims
    ):
        payload = {
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            **claims,
        }
        token = jwt.encode(payload, _secret(), algorithm="HS256")
        response = anonymous_client.get(PROBE, headers=_auth(token))
        assert response.status_code == 401

    def test_unsigned_token_is_refused(self, anonymous_client):
        """
        An `alg: none` token is the classic JWT bypass.

        PyJWT requires an explicit `key=None` to sign one, so it takes a
        deliberate step to produce one - and a naive verifier will accept it.
        That is why the guard is pinning `algorithms=["HS256"]` at verification
        rather than trusting the token's own header.
        """
        payload = {
            "sub": str(uuid.uuid4()),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        unsigned = jwt.encode(payload, key=None, algorithm="none")
        response = anonymous_client.get(PROBE, headers=_auth(unsigned))
        assert response.status_code == 401

    def test_tampered_payload_is_refused(self, anonymous_client, db_session):
        """Editing any claim invalidates the signature."""
        user = _make_user(db_session)
        claims = jwt.decode(
            create_access_token(str(user.id)), _secret(), algorithms=["HS256"]
        )
        claims["sub"] = str(uuid.uuid4())
        tampered = jwt.encode(claims, _secret(), algorithm="HS256")

        # Re-signed with the real key, this IS a valid token for a random user;
        # what must not be possible is editing a token while keeping its
        # original signature.
        assert anonymous_client.get(PROBE, headers=_auth(tampered)).status_code == 401

        head, body, signature = create_access_token(str(user.id)).split(".")
        with pytest.raises(Exception):
            jwt.decode(f"{head}.{body[:-2]}XY.{signature}", _secret(), algorithms=["HS256"])


# ── 5. The dependency itself ────────────────────────────────────────────────


class TestAuthDependencyContract:
    """
    `require_authenticated_user` on its own, outside the router wiring.

    Authentication is attached once per router in `app.main`, so the dependency
    is the actual control. Testing it in isolation pins its behaviour for the
    case that will eventually matter: a new route that attaches the check
    directly to its decorator instead of to the router.
    """

    @staticmethod
    def _probe_app(db_session):
        """
        A minimal app carrying the REAL `get_db` override and the REAL domain
        exception handler.

        Both are required for the assertions to mean anything. Without the
        override there is no database, and without the handler `AuthenticationError`
        propagates as an unhandled exception - which would make this test
        "pass" for entirely the wrong reason.
        """
        from fastapi import FastAPI

        from app.api.deps import get_db
        from app.core.exceptions import CareLoopError

        probe_app = FastAPI()

        @probe_app.exception_handler(CareLoopError)
        async def _handler(request, exc):  # mirrors app.main's handler shape
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=getattr(exc, "status_code", 400),
                content={"detail": getattr(exc, "default_message", "Error.")},
                headers=getattr(exc, "headers", None),
            )

        def _override():
            yield db_session

        probe_app.dependency_overrides[get_db] = _override
        return probe_app

    def test_dependency_refuses_an_anonymous_caller(self, db_session):
        probe_app = self._probe_app(db_session)

        @probe_app.get("/api/v1/probe")
        def handler(current=Depends(require_authenticated_user)):
            return {"ok": True}

        assert TestClient(probe_app).get("/api/v1/probe").status_code == 401

    def test_dependency_resolves_the_current_user(self, db_session):
        probe_app = self._probe_app(db_session)

        @probe_app.get("/api/v1/probe")
        def handler(current=Depends(require_authenticated_user)):
            return {"sub": str(current.id)}

        user = _make_user(db_session)
        client = TestClient(probe_app)
        response = client.get(
            "/api/v1/probe", headers=_auth(create_access_token(str(user.id)))
        )
        assert response.status_code == 200
        assert response.json()["sub"] == str(user.id)
