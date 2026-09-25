"""
CareLoop AI — Appointment API Tests
"""
import uuid


def test_create_appointment(client, sample_patient):
    patient_id = sample_patient["id"]
    payload = {
        "doctor_name": "Dr. Sarah Adams",
        "date": "2026-10-05T10:30:00Z",
        "location": "Cardiology Clinic, Room 302",
        "status": "scheduled",
        "notes": "Follow-up ECG and blood work review",
    }
    response = client.post(f"/api/v1/patients/{patient_id}/appointments", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["doctor_name"] == "Dr. Sarah Adams"
    assert data["status"] == "scheduled"
    assert data["patient_id"] == patient_id
    assert "id" in data


def test_create_appointment_invalid_status(client, sample_patient):
    patient_id = sample_patient["id"]
    payload = {
        "doctor_name": "Dr. Sarah Adams",
        "date": "2026-10-05T10:30:00Z",
        "status": "invalid_status_enum",
    }
    response = client.post(f"/api/v1/patients/{patient_id}/appointments", json=payload)
    assert response.status_code == 422


def test_list_appointments(client, sample_patient):
    patient_id = sample_patient["id"]
    client.post(
        f"/api/v1/patients/{patient_id}/appointments",
        json={
            "doctor_name": "Dr. Lee",
            "date": "2026-10-12T14:00:00Z",
            "status": "scheduled",
        },
    )
    response = client.get(f"/api/v1/patients/{patient_id}/appointments")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["doctor_name"] == "Dr. Lee"


def test_update_appointment(client, sample_patient):
    patient_id = sample_patient["id"]
    create_res = client.post(
        f"/api/v1/patients/{patient_id}/appointments",
        json={
            "doctor_name": "Dr. Gomez",
            "date": "2026-10-15T09:00:00Z",
            "status": "scheduled",
        },
    )
    apt_id = create_res.json()["id"]

    update_payload = {"status": "completed", "notes": "Patient recovering well"}
    response = client.patch(f"/api/v1/appointments/{apt_id}", json=update_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["notes"] == "Patient recovering well"


def test_delete_appointment(client, sample_patient):
    patient_id = sample_patient["id"]
    create_res = client.post(
        f"/api/v1/patients/{patient_id}/appointments",
        json={
            "doctor_name": "Dr. Gomez",
            "date": "2026-10-15T09:00:00Z",
            "status": "scheduled",
        },
    )
    apt_id = create_res.json()["id"]

    del_res = client.delete(f"/api/v1/appointments/{apt_id}")
    assert del_res.status_code == 204

    # Confirm it's removed from patient appointment list
    list_res = client.get(f"/api/v1/patients/{patient_id}/appointments")
    assert not any(a["id"] == apt_id for a in list_res.json())
