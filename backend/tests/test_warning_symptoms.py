"""
CareLoop AI — Warning Symptoms API Tests
"""
import uuid


def test_create_warning_symptom(client, sample_patient):
    patient_id = sample_patient["id"]
    payload = {
        "description": "Shortness of breath when lying flat",
        "severity": "high",
    }
    response = client.post(f"/api/v1/patients/{patient_id}/warning-symptoms", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["description"] == "Shortness of breath when lying flat"
    assert data["severity"] == "high"
    assert data["patient_id"] == patient_id
    assert "id" in data


def test_create_warning_symptom_invalid_severity(client, sample_patient):
    patient_id = sample_patient["id"]
    payload = {
        "description": "Chest pain",
        "severity": "extremely_severe",  # Invalid enum value
    }
    response = client.post(f"/api/v1/patients/{patient_id}/warning-symptoms", json=payload)
    assert response.status_code == 422


def test_list_warning_symptoms(client, sample_patient):
    patient_id = sample_patient["id"]
    client.post(
        f"/api/v1/patients/{patient_id}/warning-symptoms",
        json={"description": "Swelling in ankles", "severity": "medium"},
    )
    response = client.get(f"/api/v1/patients/{patient_id}/warning-symptoms")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["description"] == "Swelling in ankles"


def test_update_warning_symptom(client, sample_patient):
    patient_id = sample_patient["id"]
    create_res = client.post(
        f"/api/v1/patients/{patient_id}/warning-symptoms",
        json={"description": "Dizziness upon standing", "severity": "low"},
    )
    symptom_id = create_res.json()["id"]

    update_payload = {"severity": "medium"}
    response = client.patch(f"/api/v1/warning-symptoms/{symptom_id}", json=update_payload)
    assert response.status_code == 200
    assert response.json()["severity"] == "medium"


def test_delete_warning_symptom(client, sample_patient):
    patient_id = sample_patient["id"]
    create_res = client.post(
        f"/api/v1/patients/{patient_id}/warning-symptoms",
        json={"description": "Dry cough", "severity": "low"},
    )
    symptom_id = create_res.json()["id"]

    del_res = client.delete(f"/api/v1/warning-symptoms/{symptom_id}")
    assert del_res.status_code == 204

    # Confirm it's gone
    list_res = client.get(f"/api/v1/patients/{patient_id}/warning-symptoms")
    assert not any(s["id"] == symptom_id for s in list_res.json())
