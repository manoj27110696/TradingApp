from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app, mcp
from app.middleware.rate_limit import RateLimitMiddleware


def test_mcp_streamable_http_initializes_without_authentication():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }

    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(app) as client:
        response = client.post("/mcp", json=payload, headers=headers)
        headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
        initialized = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
        )
        tools = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "Options Spread Copilot"
    assert "www-authenticate" not in response.headers
    assert initialized.status_code == 202
    assert tools.status_code == 200
    assert [tool["name"] for tool in tools.json()["result"]["tools"]] == [
        "getOptionExpirations",
        "getOptionChain",
        "getFeaturedIdeas",
        "getSpreadRecommendations",
    ]


def test_mcp_exposes_only_public_research_tools():
    assert [tool.name for tool in mcp.tools] == [
        "getOptionExpirations",
        "getOptionChain",
        "getFeaturedIdeas",
        "getSpreadRecommendations",
    ]


def test_public_rate_limit_returns_retry_guidance():
    limited_app = FastAPI()
    limited_app.add_middleware(RateLimitMiddleware, requests=2, window_seconds=60)

    @limited_app.get("/api/test")
    async def limited_route():
        return {"ok": True}

    client = TestClient(limited_app)
    first = client.get("/api/test")
    second = client.get("/api/test")
    blocked = client.get("/api/test")

    assert first.status_code == 200
    assert first.headers["x-ratelimit-remaining"] == "1"
    assert second.headers["x-ratelimit-remaining"] == "0"
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1


def test_dashboard_has_no_api_key_prompt():
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "Required on Render" not in response.text
    assert 'id="apiKey"' not in response.text
