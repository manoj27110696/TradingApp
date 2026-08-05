from fastapi.testclient import TestClient
from datetime import datetime, timezone

from app.config import get_settings
from app.models import ExpirationWindow, MarketChameleonIdea
from app.main import app, calendar_expirations_for_window, ideas_provider, option_provider
from tests.test_spread_scanner import _test_chain


class StaticOptionProvider:
    async def expirations(self, symbol: str):
        return [_test_chain().expiration]

    async def chain(self, symbol: str, expiration):
        return _test_chain()


class ExpirationOutageProvider:
    async def expirations(self, symbol: str):
        raise RuntimeError("expiration endpoint unavailable")

    async def chain(self, symbol: str, expiration):
        return _test_chain()


class IncompleteExpirationProvider:
    async def expirations(self, symbol: str):
        return []

    async def chain(self, symbol: str, expiration):
        return _test_chain()


class StaticIdeasProvider:
    async def ideas(self, symbols=None):
        return [
            MarketChameleonIdea(
                symbol=symbol,
                strategy="bull put",
                title=f"{symbol} idea",
                description=f"{symbol} compact idea",
                url=f"https://example.com/{symbol.lower()}",
                fetched_at=datetime.now(timezone.utc),
            )
            for symbol in ("SPY", "QQQ", "NVDA")
        ]


def test_recommendations_fail_without_market_data_provider(monkeypatch):
    monkeypatch.delenv("APP_API_KEY", raising=False)
    monkeypatch.delenv("CUTEMARKETS_API_KEY", raising=False)
    get_settings.cache_clear()

    client = TestClient(app)
    response = client.get("/api/spreads/recommendations", params={"symbols": "SPY", "limit": 1})

    assert response.status_code == 503
    assert response.json()["detail"] == "No option-chain provider configured. Set CUTEMARKETS_API_KEY."


def test_recommendations_require_api_key_when_configured(monkeypatch):
    monkeypatch.setenv("APP_API_KEY", "secret-test-key")
    app.dependency_overrides[option_provider] = lambda: StaticOptionProvider()
    get_settings.cache_clear()

    client = TestClient(app)
    missing = client.get("/api/spreads/recommendations", params={"symbols": "SPY", "limit": 1})
    valid = client.get(
        "/api/spreads/recommendations",
        params={"symbols": "SPY", "limit": 1},
        headers={"Authorization": "Bearer secret-test-key"},
    )

    assert missing.status_code == 401
    assert valid.status_code == 200

    app.dependency_overrides.clear()
    monkeypatch.delenv("APP_API_KEY", raising=False)
    get_settings.cache_clear()


def test_recommendations_try_real_chains_when_expiration_list_fails(monkeypatch):
    monkeypatch.delenv("APP_API_KEY", raising=False)
    app.dependency_overrides[option_provider] = lambda: ExpirationOutageProvider()
    get_settings.cache_clear()

    client = TestClient(app)
    response = client.get(
        "/api/spreads/recommendations",
        params={
            "symbols": "SPY",
            "window": "custom",
            "start": "2026-05-15",
            "end": "2026-05-15",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["candidates"]
    assert "expiration list unavailable" in body["notes"][0]

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_recommendations_try_real_chains_when_expiration_list_is_incomplete(monkeypatch):
    monkeypatch.delenv("APP_API_KEY", raising=False)
    app.dependency_overrides[option_provider] = lambda: IncompleteExpirationProvider()
    get_settings.cache_clear()

    client = TestClient(app)
    response = client.get(
        "/api/spreads/recommendations",
        params={
            "symbols": "SPY",
            "window": "custom",
            "start": "2026-05-15",
            "end": "2026-05-15",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["candidates"]
    assert "expiration list had no custom matches" in body["notes"][0]

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_calendar_expiration_probe_dates_skip_weekends():
    dates = calendar_expirations_for_window(
        ExpirationWindow.custom,
        start=_test_chain().expiration,
        end=_test_chain().expiration.replace(day=17),
    )

    assert dates == [_test_chain().expiration]


def test_featured_ideas_endpoint_supports_paging(monkeypatch):
    monkeypatch.delenv("APP_API_KEY", raising=False)
    app.dependency_overrides[ideas_provider] = lambda: StaticIdeasProvider()
    get_settings.cache_clear()

    client = TestClient(app)
    response = client.get("/api/market-chameleon/ideas", params={"limit": 1, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 1
    assert body["offset"] == 1
    assert body["next_offset"] == 2
    assert body["has_more"] is True
    assert [idea["symbol"] for idea in body["ideas"]] == ["QQQ"]

    app.dependency_overrides.clear()
    get_settings.cache_clear()
