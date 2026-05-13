from abc import ABC, abstractmethod
from datetime import date

from app.models import MarketChameleonIdea, OptionChain


class OptionChainProvider(ABC):
    @abstractmethod
    async def expirations(self, symbol: str) -> list[date]:
        raise NotImplementedError

    @abstractmethod
    async def chain(self, symbol: str, expiration: date) -> OptionChain:
        raise NotImplementedError


class FeaturedIdeasProvider(ABC):
    @abstractmethod
    async def ideas(self, symbols: list[str] | None = None) -> list[MarketChameleonIdea]:
        raise NotImplementedError
