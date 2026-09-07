import asyncio
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi_mcp import FastApiMCP

from app.config import Settings, get_settings
from app.middleware.rate_limit import RateLimitMiddleware
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
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)
rate_limit_settings = get_settings()
app.add_middleware(
    RateLimitMiddleware,
    requests=rate_limit_settings.public_rate_limit_requests,
    window_seconds=rate_limit_settings.public_rate_limit_window_seconds,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@lru_cache(maxsize=8)
def _cutemarkets_provider(
    api_key: str,
    base_url: str,
    strike_window_pct: float,
    request_timeout_seconds: float,
    max_expiration_pages: int,
    cache_ttl_seconds: int,
) -> CuteMarketsOptionChainProvider:
    return CuteMarketsOptionChainProvider(
        api_key,
        base_url,
        strike_window_pct,
        request_timeout_seconds,
        max_expiration_pages,
        cache_ttl_seconds,
    )


def option_provider(settings: Settings = Depends(get_settings)) -> OptionChainProvider:
    if settings.cutemarkets_api_key:
        return _cutemarkets_provider(
            settings.cutemarkets_api_key,
            settings.cutemarkets_base_url,
            settings.cutemarkets_chain_strike_window_pct,
            settings.cutemarkets_request_timeout_seconds,
            settings.cutemarkets_max_expiration_pages,
            settings.market_data_cache_ttl_seconds,
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/api/health", operation_id="getHealth")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
        "cutemarkets_configured": bool(settings.cutemarkets_api_key),
        "market_chameleon_configured": bool(settings.market_chameleon_featured_ideas_url),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/options/expirations", operation_id="getOptionExpirations")
async def expirations(
    symbol: str = Query(..., min_length=1, max_length=12),
    provider: OptionChainProvider = Depends(option_provider),
) -> dict[str, object]:
    try:
        dates = await provider.expirations(symbol.upper())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch expirations: {exc}") from exc
    return {"symbol": symbol.upper(), "expirations": [item.isoformat() for item in dates]}


@app.get("/api/options/chain", response_model=OptionChain, operation_id="getOptionChain")
async def option_chain(
    symbol: str = Query(..., min_length=1, max_length=12),
    expiration: date = Query(...),
    provider: OptionChainProvider = Depends(option_provider),
) -> OptionChain:
    try:
        return await provider.chain(symbol.upper(), expiration)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch option chain: {exc}") from exc


@app.get("/api/market-chameleon/ideas", operation_id="getFeaturedIdeas")
async def featured_ideas(
    symbols: str | None = Query(default=None, description="Comma-separated ticker list"),
    limit: int = Query(default=5, ge=1, le=25, description="Maximum ideas to return in this page."),
    offset: int = Query(default=0, ge=0, description="Zero-based idea offset for paging through results."),
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


@app.get(
    "/api/spreads/recommendations",
    response_model=RecommendationResponse,
    operation_id="getSpreadRecommendations",
)
async def recommendations(
    symbols: str | None = Query(default=None, description="Comma-separated ticker list"),
    window: ExpirationWindow = ExpirationWindow.today,
    strategy: StrategyType = StrategyType.auto,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    limit: int = Query(default=8, ge=1, le=12),
    settings: Settings = Depends(get_settings),
    provider: OptionChainProvider = Depends(option_provider),
    featured_provider: FeaturedIdeasProvider = Depends(ideas_provider),
) -> RecommendationResponse:
    requested_symbols = parse_symbols(symbols) or settings.symbols
    symbol_list = requested_symbols[: max(1, settings.max_scan_symbols)]
    scanner = SpreadScanner()
    candidates = []
    notes = []
    if len(requested_symbols) > len(symbol_list):
        notes.append(f"Scanning the first {len(symbol_list)} symbols to keep the request responsive.")

    semaphore = asyncio.Semaphore(max(1, settings.scan_concurrency))

    async def scan_with_limit(symbol: str):
        async with semaphore:
            return await scan_symbol(
                symbol,
                window,
                strategy,
                start,
                end,
                limit,
                provider,
                scanner,
            )

    tasks = [asyncio.create_task(scan_with_limit(symbol)) for symbol in symbol_list]
    done, pending = await asyncio.wait(tasks, timeout=max(1, settings.scan_timeout_seconds))
    for symbol, task in zip(symbol_list, tasks):
        if task in pending:
            task.cancel()
            notes.append(f"{symbol}: scan timed out before completion.")
            continue
        try:
            symbol_candidates, symbol_notes = task.result()
        except Exception as exc:
            notes.append(f"{symbol}: scan failed ({exc})")
            continue
        candidates.extend(symbol_candidates)
        notes.extend(symbol_notes)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

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


async def scan_symbol(
    symbol: str,
    window: ExpirationWindow,
    strategy: StrategyType,
    start: date | None,
    end: date | None,
    limit: int,
    provider: OptionChainProvider,
    scanner: SpreadScanner,
) -> tuple[list, list[str]]:
    candidates = []
    notes = []
    selected_from_provider = True
    try:
        available = await provider.expirations(symbol)
        selected = choose_expirations(available, window, start, end)
    except Exception as exc:
        selected = calendar_expirations_for_window(window, start, end)
        selected_from_provider = False
        notes.append(f"{symbol}: expiration list unavailable ({exc}); trying requested dates.")

    if not selected:
        selected = calendar_expirations_for_window(window, start, end)
        selected_from_provider = False
        if selected:
            notes.append(f"{symbol}: expiration list had no {window.value} matches; trying requested dates.")
        else:
            return [], [f"{symbol}: no expirations matched {window.value}"]

    expiration_limit = 2 if selected_from_provider else 7
    for expiration in selected[:expiration_limit]:
        try:
            chain = await provider.chain(symbol, expiration)
        except Exception as exc:
            notes.append(f"{symbol} {expiration.isoformat()}: could not fetch chain ({exc})")
            continue
        candidates.extend(scanner.scan(chain, strategy=strategy, limit=limit))
        if not selected_from_provider and len(candidates) >= limit:
            break

    return candidates, notes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# MCP server - exposes all routes as MCP tools at /mcp
# ---------------------------------------------------------------------------

mcp = FastApiMCP(
    app,
    name="Options Spread Copilot",
    description="Ranks options vertical spreads and surfaces Market Chameleon trade ideas.",
    include_operations=[
        "getOptionExpirations",
        "getOptionChain",
        "getFeaturedIdeas",
        "getSpreadRecommendations",
    ],
)
mcp.mount_http(mount_path="/mcp")
mcp.mount_sse(mount_path="/sse")
