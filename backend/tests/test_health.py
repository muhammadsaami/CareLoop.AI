"""
CareLoop AI — Health Check Tests
"""


def test_health_endpoint(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "ok", "service": "careloop-ai"}
    # Assert no secrets, db urls, or credentials leaked
    assert "database" not in data
    assert "secret" not in data
