from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.models import ExpirationWindow, OptionChain, RecommendationResponse, StrategyType
from app.providers.base import FeaturedIdeasProvider, OptionChainProvider
from app.providers.market_chameleon import MarketChameleonFeaturedIdeasProvider
from app.providers.sample import SampleFeaturedIdeasProvider, SampleOptionChainProvider
from app.providers.tradier import TradierOptionChainProvider
from app.services.spread_scanner import SpreadScanner, choose_expirations

app = FastAPI(
    title="Options Spread Copilot API",
    version="0.1.0",
    description="Research API for ranking options spreads and connecting a Custom GPT Action.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def option_provider(settings: Settings = Depends(get_settings)) -> OptionChainProvider:
    if settings.tradier_token:
        return TradierOptionChainProvider(settings.tradier_token, settings.tradier_base_url)
    return SampleOptionChainProvider()


def ideas_provider(settings: Settings = Depends(get_settings)) -> FeaturedIdeasProvider:
    if settings.market_chameleon_featured_ideas_url:
        return MarketChameleonFeaturedIdeasProvider(
            settings.market_chameleon_featured_ideas_url,
            settings.market_chameleon_session_cookie,
        )
    return SampleFeaturedIdeasProvider()


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/api/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
        "tradier_configured": bool(settings.tradier_token),
        "market_chameleon_configured": bool(settings.market_chameleon_featured_ideas_url),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/options/expirations")
async def expirations(
    symbol: str = Query(..., min_length=1, max_length=12),
    provider: OptionChainProvider = Depends(option_provider),
) -> dict[str, object]:
    try:
        dates = await provider.expirations(symbol.upper())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch expirations: {exc}") from exc
    return {"symbol": symbol.upper(), "expirations": [item.isoformat() for item in dates]}


@app.get("/api/options/chain", response_model=OptionChain)
async def option_chain(
    symbol: str = Query(..., min_length=1, max_length=12),
    expiration: date = Query(...),
    provider: OptionChainProvider = Depends(option_provider),
) -> OptionChain:
    try:
        return await provider.chain(symbol.upper(), expiration)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch option chain: {exc}") from exc


@app.get("/api/market-chameleon/ideas")
async def featured_ideas(
    symbols: str | None = Query(default=None, description="Comma-separated ticker list"),
    provider: FeaturedIdeasProvider = Depends(ideas_provider),
) -> dict[str, object]:
    symbol_list = parse_symbols(symbols)
    try:
        ideas = await provider.ideas(symbol_list or None)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch featured ideas: {exc}") from exc
    return {"ideas": ideas}


@app.get("/api/spreads/recommendations", response_model=RecommendationResponse)
async def recommendations(
    symbols: str | None = Query(default=None, description="Comma-separated ticker list"),
    window: ExpirationWindow = ExpirationWindow.today,
    strategy: StrategyType = StrategyType.auto,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    settings: Settings = Depends(get_settings),
    provider: OptionChainProvider = Depends(option_provider),
    featured_provider: FeaturedIdeasProvider = Depends(ideas_provider),
) -> RecommendationResponse:
    symbol_list = parse_symbols(symbols) or settings.symbols
    scanner = SpreadScanner()
    candidates = []
    notes = []

    for symbol in symbol_list:
        try:
            available = await provider.expirations(symbol)
            selected = choose_expirations(available, window, start, end)
        except Exception as exc:
            notes.append(f"{symbol}: could not fetch expirations ({exc})")
            continue
        if not selected:
            notes.append(f"{symbol}: no expirations matched {window.value}")
            continue
        for expiration in selected[:2]:
            try:
                chain = await provider.chain(symbol, expiration)
            except Exception as exc:
                notes.append(f"{symbol} {expiration.isoformat()}: could not fetch chain ({exc})")
                continue
            candidates.extend(scanner.scan(chain, strategy=strategy, limit=limit))

    try:
        ideas = await featured_provider.ideas(symbol_list)
    except Exception as exc:
        ideas = []
        notes.append(f"Market Chameleon ideas unavailable ({exc})")

    boosted = boost_featured_matches(candidates, ideas)
    return RecommendationResponse(
        generated_at=datetime.now(timezone.utc),
        window=window,
        symbols=symbol_list,
        candidates=sorted(boosted, key=lambda item: item.total_score, reverse=True)[:limit],
        featured_ideas=ideas,
        notes=notes or ["Research only. Verify live quotes, liquidity, earnings, and risk before trading."],
    )


def parse_symbols(value: str | None) -> list[str]:
    if not value:
        return []
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def boost_featured_matches(candidates, ideas):
    idea_map = {idea.symbol: idea for idea in ideas}
    for candidate in candidates:
        idea = idea_map.get(candidate.symbol)
        if not idea:
            continue
        candidate.total_score = round(min(candidate.total_score + 4.0, 100.0), 2)
        candidate.rationale.append(f"Boosted because Market Chameleon featured {idea.strategy} for {idea.symbol}.")
    return candidates
