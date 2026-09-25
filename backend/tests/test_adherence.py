"""
CareLoop AI — Adherence API Tests
"""
import uuid


def test_create_adherence_log(client, sample_patient):
    patient_id = sample_patient["id"]
    # First create a medication for this patient
    med_res = client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={"name": "Lisinopril", "dosage": "10mg", "frequency": "Daily"},
    )
    assert med_res.status_code == 201
    med_id = med_res.json()["id"]

    payload = {
        "medication_id": med_id,
        "scheduled_time": "2026-09-25T08:00:00Z",
        "taken": True,
        "taken_time": "2026-09-25T08:05:00Z",
    }
    response = client.post(f"/api/v1/patients/{patient_id}/adherence", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["patient_id"] == patient_id
    assert data["medication_id"] == med_id
    assert data["taken"] is True
    assert "id" in data


def test_create_adherence_log_wrong_patient_medication(client, sample_patient):
    patient_id = sample_patient["id"]
    # Create another patient
    res2 = client.post(
        "/api/v1/patients",
        json={"name": "Bob Brown", "contact_number": "+1-555-4444"},
    )
    patient2_id = res2.json()["id"]

    # Create medication under patient 2
    med_res = client.post(
        f"/api/v1/patients/{patient2_id}/medications",
        json={"name": "Warfarin", "dosage": "2mg", "frequency": "Daily"},
    )
    med2_id = med_res.json()["id"]

    # Try to log adherence for patient 1 with medication of patient 2
    payload = {
        "medication_id": med2_id,
        "scheduled_time": "2026-09-25T08:00:00Z",
        "taken": True,
    }
    response = client.post(f"/api/v1/patients/{patient_id}/adherence", json=payload)
    assert response.status_code == 400
    assert "Medication does not belong to this patient" in response.json()["detail"]


def test_list_adherence_logs(client, sample_patient):
    patient_id = sample_patient["id"]
    med_res = client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={"name": "Aspirin", "dosage": "81mg", "frequency": "Daily"},
    )
    med_id = med_res.json()["id"]

    client.post(
        f"/api/v1/patients/{patient_id}/adherence",
        json={
            "medication_id": med_id,
            "scheduled_time": "2026-09-25T09:00:00Z",
            "taken": False,
        },
    )
    response = client.get(f"/api/v1/patients/{patient_id}/adherence")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["taken"] is False


def test_update_adherence_log(client, sample_patient):
    patient_id = sample_patient["id"]
    med_res = client.post(
        f"/api/v1/patients/{patient_id}/medications",
        json={"name": "Metformin", "dosage": "500mg", "frequency": "Daily"},
    )
    med_id = med_res.json()["id"]

    create_res = client.post(
        f"/api/v1/patients/{patient_id}/adherence",
        json={
            "medication_id": med_id,
            "scheduled_time": "2026-09-25T12:00:00Z",
            "taken": False,
        },
    )
    adherence_id = create_res.json()["id"]

    update_payload = {
        "taken": True,
        "taken_time": "2026-09-25T12:10:00Z",
    }
    response = client.patch(f"/api/v1/adherence/{adherence_id}", json=update_payload)
    assert response.status_code == 200
    assert response.json()["taken"] is True
    assert response.json()["taken_time"] is not None
