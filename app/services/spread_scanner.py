from datetime import date, timedelta

from app.models import ExpirationWindow, OptionChain, OptionContract, OptionType, SpreadCandidate, StrategyType


def expiration_range(window: ExpirationWindow, start: date | None = None, end: date | None = None) -> tuple[date, date]:
    today = date.today()
    if window == ExpirationWindow.today:
        return today, today
    if window == ExpirationWindow.weekend:
        friday = today + timedelta(days=(4 - today.weekday()) % 7)
        sunday = friday + timedelta(days=2)
        return today, sunday
    if window == ExpirationWindow.next_week:
        next_monday = today + timedelta(days=(7 - today.weekday()) % 7)
        return next_monday, next_monday + timedelta(days=6)
    if not start or not end:
        raise ValueError("custom expiration window requires start and end dates")
    return start, end


def choose_expirations(available: list[date], window: ExpirationWindow, start: date | None = None, end: date | None = None) -> list[date]:
    range_start, range_end = expiration_range(window, start, end)
    return [item for item in sorted(available) if range_start <= item <= range_end]


class SpreadScanner:
    def scan(self, chain: OptionChain, strategy: StrategyType = StrategyType.auto, limit: int = 10) -> list[SpreadCandidate]:
        requested = (
            [StrategyType.bull_call, StrategyType.bear_call, StrategyType.bull_put, StrategyType.bear_put]
            if strategy == StrategyType.auto
            else [strategy]
        )
        candidates: list[SpreadCandidate] = []
        for item in requested:
            if item == StrategyType.bull_call:
                candidates.extend(self._debit_verticals(chain, OptionType.call, item))
            elif item == StrategyType.bear_put:
                candidates.extend(self._debit_verticals(chain, OptionType.put, item))
            elif item == StrategyType.bull_put:
                candidates.extend(self._credit_verticals(chain, OptionType.put, item))
            elif item == StrategyType.bear_call:
                candidates.extend(self._credit_verticals(chain, OptionType.call, item))
        return sorted(candidates, key=lambda candidate: candidate.total_score, reverse=True)[:limit]

    def _debit_verticals(self, chain: OptionChain, option_type: OptionType, strategy: StrategyType) -> list[SpreadCandidate]:
        contracts = self._contracts(chain, option_type)
        candidates: list[SpreadCandidate] = []
        for long_leg in contracts:
            shorts = [contract for contract in contracts if contract.strike > long_leg.strike]
            if option_type == OptionType.put:
                shorts = [contract for contract in contracts if contract.strike < long_leg.strike]
            for short_leg in shorts[:4]:
                width = abs(short_leg.strike - long_leg.strike)
                debit = round(max(long_leg.ask - short_leg.bid, 0), 2)
                if debit <= 0 or debit >= width:
                    continue
                max_profit = round(width - debit, 2)
                breakeven = long_leg.strike + debit if option_type == OptionType.call else long_leg.strike - debit
                candidates.append(self._candidate(chain, strategy, long_leg, short_leg, debit, None, max_profit, debit, breakeven))
        return candidates

    def _credit_verticals(self, chain: OptionChain, option_type: OptionType, strategy: StrategyType) -> list[SpreadCandidate]:
        contracts = self._contracts(chain, option_type)
        candidates: list[SpreadCandidate] = []
        for short_leg in contracts:
            longs = [contract for contract in contracts if contract.strike < short_leg.strike]
            if option_type == OptionType.call:
                longs = [contract for contract in contracts if contract.strike > short_leg.strike]
            for long_leg in longs[:4]:
                width = abs(short_leg.strike - long_leg.strike)
                credit = round(max(short_leg.bid - long_leg.ask, 0), 2)
                if credit <= 0 or credit >= width:
                    continue
                max_loss = round(width - credit, 2)
                breakeven = short_leg.strike - credit if option_type == OptionType.put else short_leg.strike + credit
                candidates.append(self._candidate(chain, strategy, long_leg, short_leg, None, credit, credit, max_loss, breakeven))
        return candidates

    def _candidate(
        self,
        chain: OptionChain,
        strategy: StrategyType,
        long_leg: OptionContract,
        short_leg: OptionContract,
        debit: float | None,
        credit: float | None,
        max_profit: float,
        max_loss: float,
        breakeven: float,
    ) -> SpreadCandidate:
        width = abs(short_leg.strike - long_leg.strike)
        liquidity = self._liquidity_score(long_leg, short_leg)
        edge = self._edge_score(chain.underlying_price, strategy, long_leg, short_leg, max_profit, max_loss)
        reward_to_risk = round(max_profit / max(max_loss, 0.01), 2)
        total = round(edge * 0.55 + liquidity * 0.3 + min(reward_to_risk, 3.0) * 5, 2)
        rationale = [
            f"{strategy.value.replace('_', ' ')} expiring {chain.expiration.isoformat()}",
            f"Risk/reward {reward_to_risk}:1 with ${max_loss * 100:.0f} max loss per spread",
            f"Liquidity score {liquidity:.1f}/100 from bid/ask width, volume, and open interest",
        ]
        warnings = []
        if liquidity < 35:
            warnings.append("Thin liquidity; verify live quotes before considering any order.")
        if min(long_leg.bid, short_leg.bid) <= 0:
            warnings.append("One leg has no bid; pricing may be unreliable.")
        return SpreadCandidate(
            symbol=chain.symbol,
            strategy=strategy,
            expiration=chain.expiration,
            long_leg=long_leg,
            short_leg=short_leg,
            net_debit=debit,
            net_credit=credit,
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            breakeven=round(breakeven, 2),
            width=round(width, 2),
            reward_to_risk=reward_to_risk,
            liquidity_score=round(liquidity, 2),
            edge_score=round(edge, 2),
            total_score=total,
            rationale=rationale,
            warnings=warnings,
        )

    def _contracts(self, chain: OptionChain, option_type: OptionType) -> list[OptionContract]:
        return sorted(
            [contract for contract in chain.contracts if contract.option_type == option_type and contract.ask > 0],
            key=lambda contract: contract.strike,
        )

    def _liquidity_score(self, long_leg: OptionContract, short_leg: OptionContract) -> float:
        legs = (long_leg, short_leg)
        score = 0.0
        for leg in legs:
            mid = max(leg.mid, 0.01)
            width_penalty = min((leg.ask - leg.bid) / mid, 1.0)
            volume = min((leg.volume or 0) / 500, 1.0)
            open_interest = min((leg.open_interest or 0) / 1000, 1.0)
            score += (1 - width_penalty) * 35 + volume * 8 + open_interest * 7
        return max(0.0, min(score, 100.0))

    def _edge_score(
        self,
        underlying_price: float,
        strategy: StrategyType,
        long_leg: OptionContract,
        short_leg: OptionContract,
        max_profit: float,
        max_loss: float,
    ) -> float:
        risk_reward = min(max_profit / max(max_loss, 0.01), 3.0) / 3.0
        center = (long_leg.strike + short_leg.strike) / 2
        distance = abs(center - underlying_price) / max(underlying_price, 1)
        proximity = max(0.0, 1 - distance * 8)
        delta_quality = self._delta_quality(strategy, long_leg, short_leg)
        return max(0.0, min(100.0, risk_reward * 35 + proximity * 35 + delta_quality * 30))

    def _delta_quality(self, strategy: StrategyType, long_leg: OptionContract, short_leg: OptionContract) -> float:
        short_delta = abs(short_leg.delta or 0.3)
        long_delta = abs(long_leg.delta or 0.2)
        if strategy in (StrategyType.bull_put, StrategyType.bear_call):
            return max(0.0, 1 - abs(short_delta - 0.25) / 0.35)
        return max(0.0, 1 - abs((long_delta - short_delta) - 0.18) / 0.4)
