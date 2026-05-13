import asyncio
from datetime import date

from app.models import ExpirationWindow, StrategyType
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
