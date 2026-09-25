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
    random_id = str(uuid.uuid4())
    payload = {
        "name": "Lisinopril",
        "dosage": "10mg",
        "frequency": "Once daily",
    }
    response = client.post(f"/api/v1/patients/{random_id}/medications", json=payload)
    assert response.status_code == 404
    assert response.json()["detail"] == "Patient not found"


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
    random_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/medications/{random_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Medication not found"


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
