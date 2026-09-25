"""
CareLoop AI — CheckIn API Tests
"""
import uuid


def test_create_checkin(client, sample_patient):
    patient_id = sample_patient["id"]
    payload = {
        "date": "2026-09-25",
        "response_text": "Feeling good, no shortness of breath today.",
        "flagged": False,
    }
    response = client.post(f"/api/v1/patients/{patient_id}/checkins", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["date"] == "2026-09-25"
    assert data["response_text"] == "Feeling good, no shortness of breath today."
    assert data["flagged"] is False
    assert data["patient_id"] == patient_id
    assert "id" in data


def test_create_flagged_checkin(client, sample_patient):
    patient_id = sample_patient["id"]
    payload = {
        "date": "2026-09-25",
        "response_text": "Severe chest tightness noticed after dinner.",
        "flagged": True,
        "flag_reason": "Manual patient flag for testing",
    }
    response = client.post(f"/api/v1/patients/{patient_id}/checkins", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["flagged"] is True
    assert data["flag_reason"] == "Manual patient flag for testing"


def test_list_checkins(client, sample_patient):
    patient_id = sample_patient["id"]
    client.post(
        f"/api/v1/patients/{patient_id}/checkins",
        json={"date": "2026-09-24", "response_text": "Mild fatigue"},
    )
    response = client.get(f"/api/v1/patients/{patient_id}/checkins")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["response_text"] == "Mild fatigue"


def test_get_checkin_by_id(client, sample_patient):
    patient_id = sample_patient["id"]
    create_res = client.post(
        f"/api/v1/patients/{patient_id}/checkins",
        json={"date": "2026-09-23", "response_text": "All clear"},
    )
    checkin_id = create_res.json()["id"]

    response = client.get(f"/api/v1/checkins/{checkin_id}")
    assert response.status_code == 200
    assert response.json()["id"] == checkin_id
    assert response.json()["response_text"] == "All clear"


def test_get_checkin_not_found(client):
    random_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/checkins/{random_id}")
    assert response.status_code == 404
