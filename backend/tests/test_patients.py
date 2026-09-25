"""
CareLoop AI — Patient API Tests
"""
import uuid


def test_create_patient(client):
    payload = {
        "name": "Alice Smith",
        "contact_number": "+1-555-1111",
        "caregiver_contact": "+1-555-2222",
        "discharge_date": "2026-09-15",
    }
    response = client.post("/api/v1/patients", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Alice Smith"
    assert data["contact_number"] == "+1-555-1111"
    assert data["caregiver_contact"] == "+1-555-2222"
    assert data["discharge_date"] == "2026-09-15"
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


def test_create_patient_invalid_input(client):
    # Missing required name and contact_number
    response = client.post("/api/v1/patients", json={})
    assert response.status_code == 422


def test_list_patients(client, sample_patient):
    response = client.get("/api/v1/patients")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["id"] == sample_patient["id"]


def test_get_patient_by_id(client, sample_patient):
    patient_id = sample_patient["id"]
    response = client.get(f"/api/v1/patients/{patient_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == patient_id
    assert data["name"] == sample_patient["name"]


def test_get_patient_not_found(client):
    random_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/patients/{random_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Patient not found"


def test_update_patient(client, sample_patient):
    patient_id = sample_patient["id"]
    update_payload = {
        "name": "Jane Doe Updated",
        "caregiver_contact": "+1-555-9999",
    }
    response = client.patch(f"/api/v1/patients/{patient_id}", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Jane Doe Updated"
    assert data["caregiver_contact"] == "+1-555-9999"
    # Unmodified fields should remain intact
    assert data["contact_number"] == sample_patient["contact_number"]


def test_update_patient_not_found(client):
    random_id = str(uuid.uuid4())
    response = client.patch(f"/api/v1/patients/{random_id}", json={"name": "No One"})
    assert response.status_code == 404


def test_delete_patient(client, sample_patient):
    patient_id = sample_patient["id"]
    response = client.delete(f"/api/v1/patients/{patient_id}")
    assert response.status_code == 204

    # Verify patient is gone
    get_res = client.get(f"/api/v1/patients/{patient_id}")
    assert get_res.status_code == 404


def test_delete_patient_not_found(client):
    random_id = str(uuid.uuid4())
    response = client.delete(f"/api/v1/patients/{random_id}")
    assert response.status_code == 404


def test_delete_patient_with_records_conflict(client, sample_patient):
    patient_id = sample_patient["id"]
    # Add a medication to this patient
    med_payload = {
        "name": "Amoxicillin",
        "dosage": "500mg",
        "frequency": "Three times daily",
    }
    med_res = client.post(f"/api/v1/patients/{patient_id}/medications", json=med_payload)
    assert med_res.status_code == 201

    # Attempting to delete patient should return 409 Conflict
    del_res = client.delete(f"/api/v1/patients/{patient_id}")
    assert del_res.status_code == 409
    assert "Cannot delete patient with existing records" in del_res.json()["detail"]
