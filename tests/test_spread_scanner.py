import asyncio
from datetime import date, datetime, timezone

from app.models import ExpirationWindow, OptionChain, OptionContract, OptionType, StrategyType
from app.providers.cutemarkets import CuteMarketsOptionChainProvider
from app.services.spread_scanner import SpreadScanner, choose_expirations


def test_scanner_returns_ranked_candidates_for_chain():
    candidates = asyncio.run(_scan_candidates())

    assert candidates
    assert candidates == sorted(candidates, key=lambda item: item.total_score, reverse=True)
    assert all(candidate.max_loss > 0 for candidate in candidates)
    assert all(candidate.long_leg.symbol != candidate.short_leg.symbol for candidate in candidates)


async def _scan_candidates():
    chain = _test_chain()
    return SpreadScanner().scan(chain, strategy=StrategyType.auto, limit=5)


def _test_chain() -> OptionChain:
    expiration = date(2026, 5, 15)
    contracts = []
    for strike, call_bid, call_ask, put_bid, put_ask, call_delta, put_delta in (
        (515, 8.3, 8.5, 1.2, 1.3, 0.72, -0.18),
        (520, 4.9, 5.1, 2.4, 2.55, 0.52, -0.31),
        (525, 2.2, 2.35, 5.0, 5.2, 0.33, -0.49),
        (530, 0.95, 1.05, 8.2, 8.45, 0.19, -0.67),
    ):
        contracts.append(
            OptionContract(
                symbol=f"SPY260515C{strike}",
                underlying="SPY",
                expiration=expiration,
                strike=strike,
                option_type=OptionType.call,
                bid=call_bid,
                ask=call_ask,
                volume=900,
                open_interest=2500,
                delta=call_delta,
            )
        )
        contracts.append(
            OptionContract(
                symbol=f"SPY260515P{strike}",
                underlying="SPY",
                expiration=expiration,
                strike=strike,
                option_type=OptionType.put,
                bid=put_bid,
                ask=put_ask,
                volume=850,
                open_interest=2200,
                delta=put_delta,
            )
        )
    return OptionChain(
        symbol="SPY",
        underlying_price=522.0,
        expiration=expiration,
        fetched_at=datetime.now(timezone.utc),
        contracts=contracts,
        source="test",
    )


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
