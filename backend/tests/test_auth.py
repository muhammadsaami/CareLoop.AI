"""
CareLoop AI — Phase 8A: Self-service Authentication

Covers registration, login, and the identity endpoint, plus the invariants the
task calls out explicitly:

* A password is stored hashed, never in plaintext, and never echoed back.
* Every login failure returns ONE identical 401, so the endpoint cannot be used
  to enumerate registered emails.
* A freshly registered account holds exactly one `self` grant - to the patient
  the registration itself created - and nothing else, so it can read its own
  record and nothing else's.
* Existing grants (caregiver/care_team, including their revocation) and the
  operator-shape accounts keep working through the new login path.

Registration runs through an unauthenticated client so the account exists before
any credential follows; everything that needs an identity then uses the token
the registration/login actually returned.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.main import app as fastapi_app
from app.models.patient import Patient
from app.models.user import AccessRelationship, AppUser, PatientAccess
from app.services.access_control import AccessControlService

PASSWORD = "correct-horse-battery-staple"
EMAIL = "alice.patient@example.com"
FULL_NAME = "Alice Patient"


def make_payload(**overrides) -> dict:
    """A well-formed registration body, with optional per-test overrides."""
    payload = {
        "full_name": FULL_NAME,
        "email": EMAIL,
        "password": PASSWORD,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def anonymous(db_session):
    """
    An unauthenticated TestClient on the test database.
    """
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def authed():
    """
    Factory for a TestClient that presents a bearer token on every request.
    """
    def _make(token: str):
        client = TestClient(fastapi_app)
        client.headers.update({"Authorization": f"Bearer {token}"})
        return client

    return _make


def _register(anonymous, payload):
    return anonymous.post("/api/v1/auth/register", json=payload)


def _find_user(db_session, *, email=EMAIL) -> AppUser:
    return db_session.execute(
        select(AppUser).where(AppUser.email == email)
    ).scalars().one()


# ── Registration ─────────────────────────────────────────────────────────────


def test_register_creates_account_patient_and_self_grant(anonymous, db_session):
    response = _register(anonymous, make_payload())
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["patient_id"]

    user = body["user"]
    assert user["email"] == EMAIL
    assert user["full_name"] == FULL_NAME
    assert user["is_active"] is True
    assert user["patient_id"] == body["patient_id"]

    # The token actually authenticates the account it describes.
    me = anonymous.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["id"] == user["id"]

    # A real AppUser, Patient, and one `self` grant, all wired together.
    db_user = db_session.get(AppUser, uuid.UUID(user["id"]))
    assert db_user is not None
    assert db_user.email == EMAIL

    patient = db_session.get(Patient, uuid.UUID(body["patient_id"]))
    assert patient is not None
    assert patient.name == FULL_NAME
    assert patient.contact_number is None  # registration collects no phone

    grants = db_session.execute(
        select(PatientAccess).where(PatientAccess.user_id == db_user.id)
    ).scalars().all()
    assert len(grants) == 1
    assert grants[0].relationship == AccessRelationship.SELF
    assert grants[0].revoked_at is None
    assert grants[0].patient_id == patient.id


def test_register_response_never_contains_a_password(anonymous):
    response = _register(anonymous, make_payload())
    assert response.status_code == 201
    assert "password" not in response.json()
    assert "password_hash" not in response.json()


def test_password_is_never_stored_in_plaintext(anonymous, db_session):
    _register(anonymous, make_payload())
    db_user = _find_user(db_session)
    assert db_user.password_hash != PASSWORD
    assert db_user.password_hash.startswith("$2")  # a bcrypt digest
    assert verify_password(PASSWORD, db_user.password_hash)
    assert not verify_password("wrong-password", db_user.password_hash)


def test_register_does_not_set_system_access(anonymous, db_session):
    _register(anonymous, make_payload())
    assert _find_user(db_session).system_access is False


def test_duplicate_email_is_rejected_with_409(anonymous):
    assert _register(anonymous, make_payload()).status_code == 201
    second = _register(anonymous, make_payload())
    assert second.status_code == 409
    assert second.json()["detail"] == "An account with this email already exists."


def test_email_and_full_name_are_normalized(anonymous, db_session):
    payload = make_payload()
    payload["email"] = "   Alice.Patient@Example.COM   "
    payload["full_name"] = "   Alice   Patient   "
    assert _register(anonymous, payload).status_code == 201

    db_user = db_session.execute(select(AppUser)).scalars().one()
    assert db_user.email == EMAIL  # stripped and lowercased
    assert db_user.full_name == "Alice   Patient"  # edges stripped, inner kept


def test_register_canonicalizes_case_for_uniqueness(anonymous):
    assert _register(anonymous, make_payload()).status_code == 201
    variant = make_payload()
    variant["email"] = "ALICE.PATIENT@EXAMPLE.COM"
    assert _register(anonymous, variant).status_code == 409


def test_register_rejects_a_weak_password(anonymous):
    payload = make_payload()
    payload["password"] = "short"
    response = _register(anonymous, payload)
    assert response.status_code == 422


def test_register_rejects_an_overlong_password(anonymous):
    payload = make_payload()
    payload["password"] = "x" * 80  # 80 bytes > bcrypt's 72-byte window
    response = _register(anonymous, payload)
    assert response.status_code == 422


def test_register_rejects_missing_fields(anonymous):
    response = _register(anonymous, {"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 422


# ── Login ────────────────────────────────────────────────────────────────────


def _make_account(db_session, *, email=EMAIL, password=PASSWORD, is_active=True):
    user = AppUser(
        email=email.lower(),
        password_hash=hash_password(password),
        is_active=is_active,
        system_access=False,
        full_name=FULL_NAME,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_login_success_returns_a_session(anonymous, db_session):
    _register(anonymous, make_payload())
    response = anonymous.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == EMAIL
    assert body["user"]["id"]

    # The login token is immediately usable.
    me = anonymous.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL


def test_login_normalizes_email_case_and_whitespace(anonymous, db_session):
    _register(anonymous, make_payload())
    response = anonymous.post(
        "/api/v1/auth/login",
        json={"email": "  ALICE.Patient@Example.COM ", "password": PASSWORD},
    )
    assert response.status_code == 200


GENERIC_401 = "Invalid email or password."


def test_login_unknown_email_returns_a_generic_401(anonymous):
    response = anonymous.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": PASSWORD},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": GENERIC_401}


def test_login_wrong_password_returns_an_identical_401(anonymous, db_session):
    _register(anonymous, make_payload())
    unknown = anonymous.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": PASSWORD},
    )
    wrong = anonymous.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": "this-is-not-the-password"},
    )
    assert wrong.status_code == 401
    assert wrong.json() == unknown.json()  # byte-for-byte indistinguishable


def test_login_for_a_deactivated_account_returns_the_same_401(anonymous, db_session):
    _make_account(db_session, is_active=False)
    response = anonymous.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": GENERIC_401}


def test_login_accepts_a_cli_shaped_account(anonymous, db_session):
    """
    An account the operator CLI would create (lowercased email, bcrypt hash)
    authenticates through the new login endpoint.
    """
    _make_account(db_session, email="Operator.Unit@Example.com")
    response = anonymous.post(
        "/api/v1/auth/login",
        json={"email": "operator.unit@example.com", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text


# ── /auth/me ────────────────────────────────────────────────────────────────


def test_me_requires_authentication(anonymous):
    assert anonymous.get("/api/v1/auth/me").status_code == 401


def test_me_rejects_a_malformed_token(anonymous):
    response = anonymous.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401


def test_me_rejects_an_expired_token(anonymous, db_session):
    user = _make_account(db_session)
    token = create_access_token(str(user.id), expires_minutes=-1)
    response = anonymous.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


def test_me_returns_the_account_with_its_self_patient(anonymous, db_session):
    body = _register(anonymous, make_payload()).json()
    token = body["access_token"]
    me = anonymous.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["id"] == body["user"]["id"]
    assert me.json()["email"] == EMAIL
    assert me.json()["full_name"] == FULL_NAME
    assert me.json()["patient_id"] == body["patient_id"]


# ── Grant integrity: own data yes, other patients no ────────────────────────


def test_registered_patient_can_read_their_own_record(anonymous, authed):
    body = _register(anonymous, make_payload()).json()
    client = authed(body["access_token"])
    response = client.get(f"/api/v1/patients/{body['patient_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["name"] == FULL_NAME


def test_registered_patient_cannot_read_another_patient(
    anonymous, authed, db_session, no_automatic_grants, make_patient
):
    other = make_patient(name="Somebody Else", suffix="9999")
    body = _register(anonymous, make_payload()).json()
    client = authed(body["access_token"])
    response = client.get(f"/api/v1/patients/{other.id}")
    assert response.status_code == 403


def test_registration_grants_access_to_exactly_one_patient(anonymous, db_session):
    _register(anonymous, make_payload())
    db_user = _find_user(db_session)
    accessible = AccessControlService(db_session).accessible_patient_ids(db_user)
    granted = db_session.execute(
        select(PatientAccess.patient_id)
    ).scalars().all()
    assert len(accessible) == 1
    assert accessible == set(granted)


def test_login_sets_the_self_patient_id(anonymous, db_session):
    registered = _register(anonymous, make_payload()).json()
    login = anonymous.post(
        "/api/v1/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    ).json()
    assert login["patient_id"] == registered["patient_id"]
    assert login["user"]["patient_id"] == registered["patient_id"]


# ── Existing grant semantics hold through the new login path ────────────────


def test_caregiver_account_can_log_in_and_read_its_granted_patient(
    anonymous, authed, db_session, no_automatic_grants, make_patient
):
    patient = make_patient(name="Granted Patient", suffix="1234")
    caregiver = _make_account(
        db_session, email="caregiver@example.com", password="a-long-cg-password-1"
    )
    AccessControlService(db_session).grant_access(
        caregiver, patient.id, AccessRelationship.CAREGIVER
    )

    login = anonymous.post(
        "/api/v1/auth/login",
        json={"email": "caregiver@example.com", "password": "a-long-cg-password-1"},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    # A caregiver holds no `self` grant, so the account reports no own patient.
    assert body["patient_id"] is None

    client = authed(body["access_token"])
    response = client.get(f"/api/v1/patients/{patient.id}")
    assert response.status_code == 200, response.text


def test_revoked_grant_stops_working_after_relogin(
    anonymous, authed, db_session, no_automatic_grants, make_patient
):
    patient = make_patient(name="Revoked Patient", suffix="5555")
    caregiver = _make_account(
        db_session, email="revoked@example.com", password="a-long-rv-password-1"
    )
    access = AccessControlService(db_session)
    access.grant_access(caregiver, patient.id, AccessRelationship.CAREGIVER)

    first_token = anonymous.post(
        "/api/v1/auth/login",
        json={"email": "revoked@example.com", "password": "a-long-rv-password-1"},
    ).json()["access_token"]
    assert authed(first_token).get(f"/api/v1/patients/{patient.id}").status_code == 200

    assert access.revoke_access(caregiver, patient.id) is True

    second_token = anonymous.post(
        "/api/v1/auth/login",
        json={"email": "revoked@example.com", "password": "a-long-rv-password-1"},
    ).json()["access_token"]
    response = authed(second_token).get(f"/api/v1/patients/{patient.id}")
    assert response.status_code == 403


# ── Surface discipline ───────────────────────────────────────────────────────


def _api_paths():
    from fastapi.routing import APIRoute

    return {r.path for r in fastapi_app.routes if isinstance(r, APIRoute)}


def test_no_fake_logout_or_token_mint_endpoint_exists():
    """
    A logout without token revocation would be a no-op pretending to protect,
    and an HTTP token-mint endpoint would duplicate the operator CLI outside the
    operator surface.  Neither may silently appear.
    """
    paths = _api_paths()
    assert "/api/v1/auth/logout" not in paths
    assert "/api/v1/generate-token" not in paths
    assert "/api/v1/mint-token" not in paths
