"""
CareLoop AI — Patient Authorization Security Tests

Covers the access decision itself: which principal may read or write which
patient's record, and what the refusal reveals.

Two distinctions drive every assertion here, and they are easy to conflate:

  * PATIENT-keyed request - the caller names a `patient_id`.  There is nothing
    to look up, so an ungranted caller is refused 403.  Returning 404 would
    mean first checking whether that patient exists, which is the leak.

  * RESOURCE-keyed request - the caller names a `*_id`.  The row has to be read
    to learn who owns it, so an ungranted caller gets 404, byte-identical to
    the response for an id that does not exist.  Returning 403 would confirm the
    resource is real.

The tests below assert the *equality* of those responses rather than just their
status codes, because "both are 404" is the property; "this one is 404" is not.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.user import AccessRelationship, AppUser, PatientAccess
from app.services.access_control import AccessControlService


# ── Fixtures: two genuinely separate principals ─────────────────────────────


def _principal(db_session, label: str, **overrides) -> AppUser:
    """A user who shares no grants with anyone else in this test."""
    fields = {
        "email": f"{label}-{uuid.uuid4().hex[:10]}@example.invalid",
        "password_hash": hash_password("a-long-enough-password"),
        "is_active": True,
        "system_access": False,
    }
    fields.update(overrides)
    user = AppUser(**fields)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _client_for(db_session, user: AppUser) -> TestClient:
    """A TestClient acting as `user`, with no automatic patient grants.

    Automatic grants exist so the ~1,100 pre-existing assertions keep working.
    They would silently grant this principal access to every patient the test
    creates, which would make every denial below pass for the wrong reason. So
    these tests build patients and grants explicitly and never request
    `auth_headers`.
    """

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    client = TestClient(app)
    client.headers.update(
        {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    )
    return client


@pytest.fixture
def patients(db_session):
    """Two patients, created with no grants attached to anyone."""
    from app.models.patient import Patient

    made = []
    for index, name in enumerate(("Alice", "Bob")):
        patient = Patient(
            name=name,
            contact_number=f"+1555000{index:04d}",
        )
        db_session.add(patient)
        db_session.commit()
        db_session.refresh(patient)
        made.append(patient)
    return made


def _grant(db_session, user: AppUser, patient, relationship: str) -> PatientAccess:
    access = AccessControlService(db_session)
    return access.grant_access(user, patient.id, relationship)


# ── 1. No grant, no access ──────────────────────────────────────────────────


class TestUngrantedPrincipalIsRefused:
    def test_patient_list_is_empty_rather_than_forbidden(self, db_session, patients):
        """
        A principal with no grants gets an empty list, not a 403.

        The list endpoint is not itself a request for one patient, so refusing
        it would be wrong. Returning everything but the caller's patients is
        what makes the endpoint safe; the assertion is that nothing leaks into
        that list.
        """
        user = _principal(db_session, "nobody")
        with _client_for(db_session, user) as client:
            assert client.get("/api/v1/patients").status_code == 200
            assert client.get("/api/v1/patients").json() == []

    @pytest.mark.parametrize(
        "method,path_template,body",
        [
            ("get", "/api/v1/patients/{pid}", None),
            ("patch", "/api/v1/patients/{pid}", {"name": "Renamed"}),
            ("delete", "/api/v1/patients/{pid}", None),
            ("get", "/api/v1/patients/{pid}/medications", None),
            ("post", "/api/v1/patients/{pid}/medications",
             {"name": "Lisinopril", "dosage": "10mg", "frequency": "Daily"}),
            ("get", "/api/v1/patients/{pid}/adherence", None),
            ("get", "/api/v1/patients/{pid}/reminders", None),
            ("get", "/api/v1/patients/{pid}/notifications", None),
            ("get", "/api/v1/patients/{pid}/checkins/daily", None),
            ("get", "/api/v1/patients/{pid}/discharge-documents", None),
        ],
    )
    def test_patient_keyed_route_is_403(
        self, db_session, patients, method, path_template, body
    ):
        """Every patient-keyed route refuses an ungranted caller identically."""
        user = _principal(db_session, "denied")
        path = path_template.format(pid=patients[0].id)
        with _client_for(db_session, user) as client:
            response = getattr(client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == 403, f"{method.upper()} {path} -> {response.text}"

    def test_refusal_does_not_disclose_patient_existence(self, db_session, patients):
        """
        A real-but-ungranted patient and a nonexistent id answer identically.

        If these differed, the endpoint would be a patient-enumeration oracle:
        iterate over candidate ids and collect the ones that do not return 403.
        """
        user = _principal(db_session, "enumerator")
        with _client_for(db_session, user) as client:
            real = client.get(f"/api/v1/patients/{patients[0].id}")
            fake = client.get(f"/api/v1/patients/{uuid.uuid4()}")

        assert real.status_code == fake.status_code == 403
        assert real.json() == fake.json()


# ── 2. A grant is what makes it allowed ─────────────────────────────────────


class TestGrantedPrincipalIsAllowed:
    @pytest.mark.parametrize("relationship", sorted(AccessRelationship.ALL))
    def test_every_relationship_grants_read(
        self, db_session, patients, relationship
    ):
        """
        `self`, `caregiver` and `care_team` all read the same patient.

        The three relationships are recorded for audit and for the CLI; none of
        them is a lesser role, and no relationship is a write role. Access is
        per-patient, never global.
        """
        user = _principal(db_session, f"rel-{relationship}")
        _grant(db_session, user, patients[0], relationship)

        with _client_for(db_session, user) as client:
            response = client.get(f"/api/v1/patients/{patients[0].id}")

        assert response.status_code == 200
        assert response.json()["id"] == str(patients[0].id)

    def test_grant_to_one_patient_does_not_reach_another(self, db_session, patients):
        """Access is per-patient, not per-user: one grant, one patient."""
        user = _principal(db_session, "one-of-two")
        _grant(db_session, user, patients[0], AccessRelationship.CAREGIVER)

        with _client_for(db_session, user) as client:
            allowed = client.get(f"/api/v1/patients/{patients[0].id}")
            refused = client.get(f"/api/v1/patients/{patients[1].id}")
            listed = client.get("/api/v1/patients").json()

        assert allowed.status_code == 200
        assert refused.status_code == 403
        assert [p["id"] for p in listed] == [str(patients[0].id)]

    def test_revoking_a_grant_takes_effect_immediately(self, db_session, patients):
        """A revoked grant stops working on the next request, not at token expiry."""
        user = _principal(db_session, "ex-caregiver")
        _grant(db_session, user, patients[0], AccessRelationship.CAREGIVER)
        with _client_for(db_session, user) as client:
            assert client.get(f"/api/v1/patients/{patients[0].id}").status_code == 200

            AccessControlService(db_session).revoke_access(user, patients[0].id)
            db_session.commit()

            assert client.get(f"/api/v1/patients/{patients[0].id}").status_code == 403

    def test_revocation_is_not_resurrected_by_a_stale_grant_row(
        self, db_session, patients
    ):
        """
        A revoked row stays revoked even if the same grant is written again.

        The replacement is a new row, so the audit trail retains the fact that
        access was withdrawn. If a query ever filtered on `user_id`/`patient_id`
        alone instead of `revoked_at IS NULL`, the old row would re-appear and
        access would silently return.
        """
        user = _principal(db_session, "churn")
        access = AccessControlService(db_session)
        first = access.grant_access(user, patients[0].id, AccessRelationship.CAREGIVER)
        access.revoke_access(user, patients[0].id)
        db_session.commit()
        access.grant_access(user, patients[0].id, AccessRelationship.CAREGIVER)
        db_session.commit()

        rows = (
            db_session.query(PatientAccess)
            .filter(PatientAccess.user_id == user.id)
            .all()
        )
        assert len(rows) == 2, "re-granting after revocation must not rewrite history"
        assert sum(1 for row in rows if row.revoked_at is None) == 1
        assert first.revoked_at is not None
        assert access.has_access(user, patients[0].id) is True


# ── 3. Resource-keyed requests must not confirm existence ───────────────────


class TestResourceKeyedRoutesDoNotDiscloseExistence:
    def test_medication_of_another_patient_is_indistinguishable_from_a_missing_one(
        self, db_session, patients
    ):
        """
        Build a real medication for Alice, then have Bob's principal ask for it.

        Bob's token has no grants at all, so the authorization layer cannot tell
        a forbidden medication from a nonexistent one - and must not be able to.
        This is the assertion that the medication-by-id endpoint is not an
        existence oracle.
        """
        alice = _principal(db_session, "alice-owner")
        _grant(db_session, alice, patients[0], AccessRelationship.SELF)
        with _client_for(db_session, alice) as client:
            created = client.post(
                f"/api/v1/patients/{patients[0].id}/medications",
                json={"name": "Metformin", "dosage": "500mg", "frequency": "Twice daily"},
            )
        medication_id = created.json()["id"]

        bob = _principal(db_session, "bob-probe")
        with _client_for(db_session, bob) as client:
            denied = client.get(f"/api/v1/medications/{medication_id}")
            absent = client.get(f"/api/v1/medications/{uuid.uuid4()}")

        assert denied.status_code == absent.status_code == 404
        assert denied.json() == absent.json()

    def test_another_patients_medication_cannot_be_written_through_its_id(
        self, db_session, patients
    ):
        """
        Reading a stranger's medication is not the only way to affect it.

        If the update route resolved ownership only for the GET, a caller could
        rewrite another patient's medication - changing a dose on a real record -
        while every read path looked correctly protected.
        """
        alice = _principal(db_session, "alice-med")
        _grant(db_session, alice, patients[0], AccessRelationship.SELF)
        with _client_for(db_session, alice) as client:
            medication_id = client.post(
                f"/api/v1/patients/{patients[0].id}/medications",
                json={"name": "Lisinopril", "dosage": "10mg", "frequency": "Daily"},
            ).json()["id"]

        bob = _principal(db_session, "bob-writer")
        with _client_for(db_session, bob) as client:
            patched = client.patch(
                f"/api/v1/medications/{medication_id}", json={"dosage": "999mg"}
            )
            deleted = client.delete(f"/api/v1/medications/{medication_id}")

        assert patched.status_code == 404
        assert deleted.status_code == 404

    def test_a_body_patient_id_cannot_redirect_a_write_to_another_patient(
        self, db_session, patients
    ):
        """
        Bob, granted only Alice, sends `patient_id: <Alice>` in a medication body
        under HIS OWN patient in the path.

        The safe outcome is not a refusal - it is that the stray body field is
        ignored and the record is written to the patient named in the path. A
        refusal would also be safe, but the record must never land on Alice,
        because the caller holds no grant to her.

        This is the same class of bug as trusting a client-supplied owner id: the
        path is the authority, and a body field must not be able to outvote it.
        """
        bob = _principal(db_session, "bob-redirect")
        _grant(db_session, bob, patients[1], AccessRelationship.CAREGIVER)
        with _client_for(db_session, bob) as client:
            created = client.post(
                f"/api/v1/patients/{patients[1].id}/medications",
                json={
                    "name": "Injected",
                    "dosage": "1mg",
                    "frequency": "Daily",
                    "patient_id": str(patients[0].id),
                },
            )

        assert created.status_code == 201
        assert created.json()["patient_id"] == str(patients[1].id), (
            "the write followed the body patient_id instead of the path; a "
            "caller could write records into any patient whose id it knew"
        )

        from app.models.medication import Medication

        leaked = (
            db_session.query(Medication)
            .filter(Medication.name == "Injected", Medication.patient_id == patients[0].id)
            .count()
        )
        assert leaked == 0, "a medication was written into a patient with no grant"

    def test_reading_another_patients_record_by_its_own_id_is_404(
        self, db_session, patients
    ):
        """
        The resource-keyed direction: Alice's medication id, Bob's token.

        Bob is granted a patient of his own in this test, so a 403 would prove
        the code had resolved the resource and then refused - which discloses
        that the medication exists. It must be indistinguishable from a
        nonexistent id.
        """
        alice = _principal(db_session, "alice-owns")
        _grant(db_session, alice, patients[0], AccessRelationship.SELF)
        with _client_for(db_session, alice) as client:
            medication_id = client.post(
                f"/api/v1/patients/{patients[0].id}/medications",
                json={"name": "Atorvastatin", "dosage": "20mg", "frequency": "Nightly"},
            ).json()["id"]

        bob = _principal(db_session, "bob-has-own")
        _grant(db_session, bob, patients[1], AccessRelationship.CAREGIVER)
        with _client_for(db_session, bob) as client:
            denied = client.get(f"/api/v1/medications/{medication_id}")
            absent = client.get(f"/api/v1/medications/{uuid.uuid4()}")
            his_own = client.get(f"/api/v1/patients/{patients[1].id}")

        assert his_own.status_code == 200, "control: Bob's own access must work"
        assert denied.status_code == absent.status_code == 404
        assert denied.json() == absent.json()


# ── 4. The whole-system endpoints ───────────────────────────────────────────


class TestSystemWideEndpoints:
    @pytest.mark.parametrize(
        "path", ["/api/v1/notifications/dispatch", "/api/v1/notifications/retry"]
    )
    def test_a_caregiver_cannot_reach_them(self, db_session, patients, path):
        """
        System endpoints need operator rights, and no patient grant substitutes.

        Dispatch walks every patient in the database, so a caregiver's grant -
        even a `care_team` grant - must not be enough.
        """
        caregiver = _principal(db_session, "caregiver-dispatch")
        _grant(db_session, caregiver, patients[0], AccessRelationship.CARE_TEAM)
        with _client_for(db_session, caregiver) as client:
            response = client.post(path, json={})
        assert response.status_code == 403

    def test_an_ordinary_grant_does_not_imply_operator_rights(self, db_session, patients):
        """`system_access` is independent of patient access, and defaults off."""
        user = _principal(db_session, "no-operator")
        _grant(db_session, user, patients[0], AccessRelationship.SELF)
        with _client_for(db_session, user) as client:
            assert client.post("/api/v1/notifications/dispatch", json={}).status_code == 403

    def test_an_operator_may_dispatch(self, db_session):
        operator = _principal(db_session, "operator", system_access=True)
        with _client_for(db_session, operator) as client:
            assert client.post("/api/v1/notifications/dispatch", json={}).status_code == 200

    def test_no_patient_scoped_route_requires_operator_rights(self, db_session, patients):
        """
        Operator rights are not spread across ordinary clinical routes.

        Requiring `system_access` on a patient route would look like defence in
        depth in review while breaking every caregiver, so it is asserted
        against directly: an operator-only principal can still use the
        patient-scoped routes.
        """
        operator = _principal(db_session, "operator-also-carer")
        _grant(db_session, operator, patients[0], AccessRelationship.SELF)
        with _client_for(db_session, operator) as client:
            assert client.get(f"/api/v1/patients/{patients[0].id}").status_code == 200
            assert client.get("/api/v1/patients").status_code == 200


# ── 5. Isolation of the list endpoints ──────────────────────────────────────


class TestListScoping:
    def test_list_never_contains_another_patients_record(self, db_session, patients):
        """
        The list is filtered in SQL, not in Python.

        A post-filter would return the right rows while still loading every
        patient's data into the process, which is a much weaker guarantee than
        the endpoint appears to offer. The size assertion below is the
        observable part; the service test covers the query shape.
        """
        alice = _principal(db_session, "alice-list")
        _grant(db_session, alice, patients[0], AccessRelationship.SELF)
        with _client_for(db_session, alice) as client:
            listed = client.get("/api/v1/patients").json()

        ids = {row["id"] for row in listed}
        assert str(patients[0].id) in ids
        assert str(patients[1].id) not in ids
        assert listed == [
            row for row in listed if row["id"] == str(patients[0].id)
        ]

    def test_patient_scoping_is_applied_to_reminders_and_notifications(
        self, db_session, patients
    ):
        """Nested list endpoints inherit the patient scope from the path."""
        user = _principal(db_session, "nested")
        _grant(db_session, user, patients[0], AccessRelationship.SELF)
        with _client_for(db_session, user) as client:
            for path in (
                f"/api/v1/patients/{patients[0].id}/reminders",
                f"/api/v1/patients/{patients[0].id}/notifications",
                f"/api/v1/patients/{patients[0].id}/discharge-documents",
            ):
                assert client.get(path).status_code == 200, path
            for path in (
                f"/api/v1/patients/{patients[1].id}/reminders",
                f"/api/v1/patients/{patients[1].id}/notifications",
                f"/api/v1/patients/{patients[1].id}/discharge-documents",
            ):
                assert client.get(path).status_code == 403, path
