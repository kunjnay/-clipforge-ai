from fastapi.testclient import TestClient
from app import app


def test_local_dashboard_and_health_routes():
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "One YouTube link" in page.text
        assert "30 different short clips" in page.text
        assert "Schedule all to both platforms" in page.text
        assert "instagram_urls" not in page.text
        assert "KLAP_API_KEY" not in page.text
        assert "OPENAI_API_KEY" not in page.text
        health = client.get("/api/health").json()
        assert health["status"] == "online"
        assert health["free_local_processing"] is True
