"""
CareLoop AI — Medication API Tests
"""
import uuid


def test_create_medication(client, sample_patient):
    patient_id = sample_patient["id"]
    payload = {
        "name": "Metformin",
        "dosage": "500mg",
        "frequency": "Twice daily",
        "timing": "After meals",
        "start_date": "2026-09-21",
        "end_date": "2026-10-21",
        "instructions": "Take with full glass of water",
    }
    response = client.post(f"/api/v1/patients/{patient_id}/medications", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Metformin"
    assert data["dosage"] == "500mg"
    assert data["patient_id"] == patient_id
    assert data["frequency"] == "Twice daily"


def test_create_medication_invalid_patient(client):
    """
    403, not 404.  The caller holds no grant to this id, and the grant lookup
    never touches `patients`, so the response must not reveal whether a patient
    with this id exists.
    """
    random_id = str(uuid.uuid4())
    payload = {
        "name": "Lisinopril",
        "dosage": "10mg",
        "frequency": "Once daily",
    }
    response = client.post(f"/api/v1/patients/{random_id}/medications", json=payload)
    assert response.status_code == 403


def test_create_medication_invalid_payload(client, sample_patient):
    patient_id = sample_patient["id"]
    # Missing dosage and frequency
    response = client.post(f"/api/v1/patients/{patient_id}/medications", json={"name": "Aspirin"})
    assert response.status_code == 422


def test_list_medications(client, sample_patient):
    patient_id = sample_patient["id"]
    payload = {
        "name": "Atorvastatin",
        "dosage": "20mg",
        "frequency": "Once at bedtime",
    }
    client.post(f"/api/v1/patients/{patient_id}/medications", json=payload)

    response = client.get(f"/api/v1/patients/{patient_id}/medications")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["name"] == "Atorvastatin"


def test_get_medication_by_id(client, sample_patient):
    patient_id = sample_patient["id"]
    payload = {
        "name": "Omeprazole",
        "dosage": "40mg",
        "frequency": "Once daily before breakfast",
    }
    create_res = client.post(f"/api/v1/patients/{patient_id}/medications", json=payload)
    med_id = create_res.json()["id"]

    response = client.get(f"/api/v1/medications/{med_id}")
    assert response.status_code == 200
    assert response.json()["id"] == med_id
    assert response.json()["name"] == "Omeprazole"


def test_get_medication_not_found(client):
    """
    Still 404, but answered by the AUTHORIZATION layer rather than the service.

    This is the resource-keyed case, and it is the opposite of the patient-keyed
    one: the caller supplied a medication id, so the row has to be resolved to
    learn who owns it, and a 403 would confirm the medication is real. The same
    404 is returned for "no such medication" and "not yours", so the id cannot
    be used to probe which medications exist.

    The generic body is a consequence of that, not a regression: the request
    never reaches `MedicationService`, so its specific "Medication not found"
    message is not the one that applies.
    """
    random_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/medications/{random_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not found."


def test_get_medication_of_another_patient_is_404(client, db_session, sample_patient):
    """
    A real medication belonging to a patient the caller has no grant to answers
    404 - identical to the response for an id that does not exist.

    This is the assertion that actually proves the endpoint is not an existence
    oracle; the previous test alone would pass even with no authorization at all.
    """
    patient_id = sample_patient["id"]
    create_res = client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={"name": "Metformin", "dosage": "500mg", "frequency": "Twice daily"},
    )
    medication_id = create_res.json()["id"]

    # Drop the grant the fixture granted, leaving the medication intact.
    from app.models.user import PatientAccess

    db_session.query(PatientAccess).filter(
        PatientAccess.patient_id == uuid.UUID(patient_id)
    ).delete()
    db_session.commit()

    denied = client.get(f"/api/v1/medications/{medication_id}")
    absent = client.get(f"/api/v1/medications/{uuid.uuid4()}")

    assert denied.status_code == 404
    assert denied.json() == absent.json()


def test_update_medication(client, sample_patient):
    patient_id = sample_patient["id"]
    create_res = client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={"name": "Amlodipine", "dosage": "5mg", "frequency": "Daily"},
    )
    med_id = create_res.json()["id"]

    update_payload = {"dosage": "10mg", "instructions": "Increased dosage per doctor"}
    response = client.patch(f"/api/v1/medications/{med_id}", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["dosage"] == "10mg"
    assert data["instructions"] == "Increased dosage per doctor"
    assert data["name"] == "Amlodipine"


def test_delete_medication(client, sample_patient):
    patient_id = sample_patient["id"]
    create_res = client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={"name": "Ibuprofen", "dosage": "400mg", "frequency": "As needed"},
    )
    med_id = create_res.json()["id"]

    del_res = client.delete(f"/api/v1/medications/{med_id}")
    assert del_res.status_code == 204

    get_res = client.get(f"/api/v1/medications/{med_id}")
    assert get_res.status_code == 404
