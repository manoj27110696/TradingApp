import asyncio
from datetime import date

from app.models import ExpirationWindow, StrategyType
from app.providers.cutemarkets import CuteMarketsOptionChainProvider
from app.providers.sample import SampleOptionChainProvider
from app.services.spread_scanner import SpreadScanner, choose_expirations


def test_scanner_returns_ranked_candidates_for_sample_chain():
    candidates = asyncio.run(_scan_sample_candidates())

    assert candidates
    assert candidates == sorted(candidates, key=lambda item: item.total_score, reverse=True)
    assert all(candidate.max_loss > 0 for candidate in candidates)
    assert all(candidate.long_leg.symbol != candidate.short_leg.symbol for candidate in candidates)


async def _scan_sample_candidates():
    provider = SampleOptionChainProvider()
    expiration = (await provider.expirations("SPY"))[0]
    chain = await provider.chain("SPY", expiration)
    return SpreadScanner().scan(chain, strategy=StrategyType.auto, limit=5)


def test_choose_expirations_custom_range():
    expirations = [date(2026, 5, 13), date(2026, 5, 15), date(2026, 5, 22)]

    selected = choose_expirations(
        expirations,
        ExpirationWindow.custom,
        start=date(2026, 5, 14),
        end=date(2026, 5, 20),
    )

    assert selected == [date(2026, 5, 15)]


def test_cutemarkets_contract_parser_uses_quote_or_day_price():
    provider = CuteMarketsOptionChainProvider("test-key", "https://api.cutemarkets.com")

    with_quote = provider._contract(
        "SPY",
        {
            "details": {
                "ticker": "O:SPY260515C00520000",
                "contract_type": "call",
                "expiration_date": "2026-05-15",
                "strike_price": 520,
            },
            "greeks": {"delta": 0.44, "gamma": 0.02, "theta": -0.05, "vega": 0.11},
            "implied_volatility": 0.21,
            "last_quote": {"bid": 1.2, "ask": 1.28},
            "day": {"volume": 750, "close": 1.24},
            "open_interest": 3500,
        },
    )
    without_quote = provider._contract(
        "SPY",
        {
            "details": {
                "ticker": "O:SPY260515P00515000",
                "contract_type": "put",
                "expiration_date": "2026-05-15",
                "strike_price": 515,
            },
            "day": {"volume": 125, "close": 0.86},
            "open_interest": 900,
        },
    )

    assert with_quote.bid == 1.2
    assert with_quote.ask == 1.28
    assert with_quote.delta == 0.44
    assert without_quote.bid == 0.86
    assert without_quote.ask == 0.86
