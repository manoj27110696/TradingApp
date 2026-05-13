from datetime import date, datetime, timedelta, timezone

from app.models import MarketChameleonIdea, OptionChain, OptionContract, OptionType
from app.providers.base import FeaturedIdeasProvider, OptionChainProvider


class SampleOptionChainProvider(OptionChainProvider):
    async def expirations(self, symbol: str) -> list[date]:
        today = date.today()
        return [today, today + timedelta(days=(4 - today.weekday()) % 7), today + timedelta(days=7)]

    async def chain(self, symbol: str, expiration: date) -> OptionChain:
        symbol = symbol.upper()
        base_prices = {
            "SPY": 520.0,
            "QQQ": 445.0,
            "IWM": 205.0,
            "AAPL": 190.0,
            "MSFT": 430.0,
            "NVDA": 910.0,
            "TSLA": 180.0,
        }
        underlying = base_prices.get(symbol, 100.0)
        strikes = [round(underlying + offset, 2) for offset in (-15, -10, -5, 0, 5, 10, 15)]
        days = max((expiration - date.today()).days, 0) + 1
        contracts: list[OptionContract] = []
        for strike in strikes:
            call_intrinsic = max(underlying - strike, 0)
            put_intrinsic = max(strike - underlying, 0)
            time_value = max(0.35, 1.15 * (days ** 0.5)) * (1 - min(abs(strike - underlying) / underlying, 0.7))
            for option_type, intrinsic, delta_sign in (
                (OptionType.call, call_intrinsic, 1),
                (OptionType.put, put_intrinsic, -1),
            ):
                mid = max(0.05, intrinsic + time_value)
                spread = max(0.05, mid * 0.06)
                distance = abs(strike - underlying) / max(underlying, 1)
                delta = delta_sign * max(0.08, min(0.92, 0.52 - distance * 2.1))
                contracts.append(
                    OptionContract(
                        symbol=f"{symbol}{expiration:%y%m%d}{'C' if option_type == OptionType.call else 'P'}{int(strike * 1000):08d}",
                        underlying=symbol,
                        expiration=expiration,
                        strike=strike,
                        option_type=option_type,
                        bid=round(max(0.01, mid - spread / 2), 2),
                        ask=round(mid + spread / 2, 2),
                        last=round(mid, 2),
                        volume=max(5, int(1200 / (1 + abs(strike - underlying)))),
                        open_interest=max(10, int(4000 / (1 + abs(strike - underlying) * 0.7))),
                        delta=round(delta, 3),
                        gamma=0.02,
                        theta=round(-0.04 * days, 3),
                        vega=0.11,
                        implied_volatility=round(0.18 + distance, 3),
                    )
                )
        return OptionChain(
            symbol=symbol,
            underlying_price=underlying,
            expiration=expiration,
            fetched_at=datetime.now(timezone.utc),
            contracts=contracts,
            source="sample",
        )


class SampleFeaturedIdeasProvider(FeaturedIdeasProvider):
    async def ideas(self, symbols: list[str] | None = None) -> list[MarketChameleonIdea]:
        now = datetime.now(timezone.utc)
        allowed = {symbol.upper() for symbol in symbols or []}
        ideas = [
            MarketChameleonIdea(
                symbol="SPY",
                strategy="bull put spread",
                expiration=date.today() + timedelta(days=7),
                title="Sample: SPY short put vertical",
                description="Placeholder featured idea used until a licensed Market Chameleon feed is configured.",
                url=None,
                confidence=0.62,
                fetched_at=now,
            ),
            MarketChameleonIdea(
                symbol="QQQ",
                strategy="bear call spread",
                expiration=date.today() + timedelta(days=7),
                title="Sample: QQQ call credit spread",
                description="Placeholder featured idea used until a licensed Market Chameleon feed is configured.",
                url=None,
                confidence=0.58,
                fetched_at=now,
            ),
        ]
        if allowed:
            return [idea for idea in ideas if idea.symbol in allowed]
        return ideas
