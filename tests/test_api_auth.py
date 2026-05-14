from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_recommendations_allow_local_without_api_key(monkeypatch):
    monkeypatch.delenv("APP_API_KEY", raising=False)
    get_settings.cache_clear()

    client = TestClient(app)
    response = client.get("/api/spreads/recommendations", params={"symbols": "SPY", "limit": 1})

    assert response.status_code == 200


def test_recommendations_require_api_key_when_configured(monkeypatch):
    monkeypatch.setenv("APP_API_KEY", "secret-test-key")
    get_settings.cache_clear()

    client = TestClient(app)
    missing = client.get("/api/spreads/recommendations", params={"symbols": "SPY", "limit": 1})
    valid = client.get(
        "/api/spreads/recommendations",
        params={"symbols": "SPY", "limit": 1},
        headers={"X-API-Key": "secret-test-key"},
    )

    assert missing.status_code == 401
    assert valid.status_code == 200

    monkeypatch.delenv("APP_API_KEY", raising=False)
    get_settings.cache_clear()
