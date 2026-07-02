from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi_mcp import FastApiMCP

from app.config import Settings, get_settings
from app.models import ExpirationWindow, OptionChain, RecommendationResponse, StrategyType
from app.providers.base import FeaturedIdeasProvider, OptionChainProvider
from app.providers.cutemarkets import CuteMarketsOptionChainProvider
from app.providers.market_chameleon import MarketChameleonFeaturedIdeasProvider
from app.services.spread_scanner import SpreadScanner, choose_expirations, expiration_range

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
    if settings.cutemarkets_api_key:
        return CuteMarketsOptionChainProvider(
            settings.cutemarkets_api_key,
            settings.cutemarkets_base_url,
            settings.cutemarkets_chain_strike_window_pct,
        )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="No option-chain provider configured. Set CUTEMARKETS_API_KEY.",
    )


def ideas_provider(settings: Settings = Depends(get_settings)) -> FeaturedIdeasProvider:
    if settings.market_chameleon_featured_ideas_url:
        return MarketChameleonFeaturedIdeasProvider(
            settings.market_chameleon_featured_ideas_url,
            settings.market_chameleon_session_cookie,
        )
    return EmptyFeaturedIdeasProvider()


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.app_api_key and x_api_key != settings.app_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/api/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
        "cutemarkets_configured": bool(settings.cutemarkets_api_key),
        "market_chameleon_configured": bool(settings.market_chameleon_featured_ideas_url),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/options/expirations")
async def expirations(
    symbol: str = Query(..., min_length=1, max_length=12),
    _auth: None = Depends(require_api_key),
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
    _auth: None = Depends(require_api_key),
    provider: OptionChainProvider = Depends(option_provider),
) -> OptionChain:
    try:
        return await provider.chain(symbol.upper(), expiration)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch option chain: {exc}") from exc


@app.get("/api/market-chameleon/ideas")
async def featured_ideas(
    symbols: str | None = Query(default=None, description="Comma-separated ticker list"),
    limit: int = Query(default=5, ge=1, le=25, description="Maximum ideas to return in this page."),
    offset: int = Query(default=0, ge=0, description="Zero-based idea offset for paging through results."),
    _auth: None = Depends(require_api_key),
    provider: FeaturedIdeasProvider = Depends(ideas_provider),
) -> dict[str, object]:
    symbol_list = parse_symbols(symbols)
    try:
        ideas = await provider.ideas(symbol_list or None)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch featured ideas: {exc}") from exc
    page = ideas[offset : offset + limit]
    next_offset = offset + limit if offset + limit < len(ideas) else None
    return {
        "ideas": page,
        "total": len(ideas),
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "has_more": next_offset is not None,
    }


@app.get("/api/spreads/recommendations", response_model=RecommendationResponse)
async def recommendations(
    symbols: str | None = Query(default=None, description="Comma-separated ticker list"),
    window: ExpirationWindow = ExpirationWindow.today,
    strategy: StrategyType = StrategyType.auto,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    limit: int = Query(default=8, ge=1, le=12),
    _auth: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    provider: OptionChainProvider = Depends(option_provider),
    featured_provider: FeaturedIdeasProvider = Depends(ideas_provider),
) -> RecommendationResponse:
    symbol_list = parse_symbols(symbols) or settings.symbols
    scanner = SpreadScanner()
    candidates = []
    notes = []

    for symbol in symbol_list:
        symbol_candidate_count = 0
        selected_from_provider = True
        try:
            available = await provider.expirations(symbol)
            selected = choose_expirations(available, window, start, end)
        except Exception as exc:
            selected = calendar_expirations_for_window(window, start, end)
            selected_from_provider = False
            notes.append(
                f"{symbol}: expiration list unavailable ({exc}); trying real chain data for requested dates."
            )
        if not selected:
            selected = calendar_expirations_for_window(window, start, end)
            selected_from_provider = False
            if selected:
                notes.append(
                    f"{symbol}: expiration list had no {window.value} matches; trying real chain data for requested dates."
                )
            else:
                notes.append(f"{symbol}: no expirations matched {window.value}")
                continue
        expiration_limit = 2 if selected_from_provider else 7
        for expiration in selected[:expiration_limit]:
            try:
                chain = await provider.chain(symbol, expiration)
            except Exception as exc:
                notes.append(f"{symbol} {expiration.isoformat()}: could not fetch chain ({exc})")
                continue
            new_candidates = scanner.scan(chain, strategy=strategy, limit=limit)
            candidates.extend(new_candidates)
            symbol_candidate_count += len(new_candidates)
            if not selected_from_provider and symbol_candidate_count >= limit:
                break

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
        featured_ideas=ideas[:5],
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


def calendar_expirations_for_window(
    window: ExpirationWindow,
    start: date | None = None,
    end: date | None = None,
    max_days: int = 14,
) -> list[date]:
    range_start, range_end = expiration_range(window, start, end)
    day_count = min((range_end - range_start).days + 1, max_days)
    return [
        expiration
        for offset in range(max(day_count, 0))
        if (expiration := range_start + timedelta(days=offset)).weekday() < 5
    ]


class EmptyFeaturedIdeasProvider(FeaturedIdeasProvider):
    async def ideas(self, symbols: list[str] | None = None) -> list:
        return []


# Mount MCP server — exposes all routes as MCP tools at /mcp
mcp = FastApiMCP(
    app,
    name="Options Spread Copilot",
    description="Ranks options vertical spreads and surfaces Market Chameleon trade ideas.",
)
mcp.mount()
