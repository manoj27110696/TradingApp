from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ExpirationWindow(str, Enum):
    today = "today"
    weekend = "weekend"
    next_week = "next_week"
    custom = "custom"


class OptionType(str, Enum):
    call = "call"
    put = "put"


class StrategyType(str, Enum):
    auto = "auto"
    bull_call = "bull_call"
    bear_call = "bear_call"
    bull_put = "bull_put"
    bear_put = "bear_put"


class OptionContract(BaseModel):
    symbol: str
    underlying: str
    expiration: date
    strike: float
    option_type: OptionType
    bid: float = 0.0
    ask: float = 0.0
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    implied_volatility: float | None = None

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return round((self.bid + self.ask) / 2, 4)
        return round(self.last or self.bid or self.ask or 0.0, 4)


class OptionChain(BaseModel):
    symbol: str
    underlying_price: float
    expiration: date
    fetched_at: datetime
    contracts: list[OptionContract]
    source: str


class SpreadCandidate(BaseModel):
    symbol: str
    strategy: StrategyType
    expiration: date
    long_leg: OptionContract
    short_leg: OptionContract
    net_debit: float | None = None
    net_credit: float | None = None
    max_profit: float
    max_loss: float
    breakeven: float
    width: float
    reward_to_risk: float
    liquidity_score: float
    edge_score: float
    total_score: float
    rationale: list[str]
    warnings: list[str] = Field(default_factory=list)


class MarketChameleonIdea(BaseModel):
    symbol: str
    strategy: str
    expiration: date | None = None
    title: str
    description: str = ""
    url: str | None = None
    confidence: float | None = None
    fetched_at: datetime


class RecommendationResponse(BaseModel):
    generated_at: datetime
    window: ExpirationWindow
    symbols: list[str]
    candidates: list[SpreadCandidate]
    featured_ideas: list[MarketChameleonIdea]
    notes: list[str]
