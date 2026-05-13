from datetime import date, datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.models import MarketChameleonIdea
from app.providers.base import FeaturedIdeasProvider


class MarketChameleonFeaturedIdeasProvider(FeaturedIdeasProvider):
    def __init__(self, featured_ideas_url: str, session_cookie: str = "") -> None:
        self.featured_ideas_url = featured_ideas_url
        self.session_cookie = session_cookie

    async def ideas(self, symbols: list[str] | None = None) -> list[MarketChameleonIdea]:
        if not self.featured_ideas_url:
            return []

        headers = {"User-Agent": "OptionsSpreadCopilot/1.0"}
        if self.session_cookie:
            headers["Cookie"] = self.session_cookie

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(self.featured_ideas_url, headers=headers)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        allowed = {symbol.upper() for symbol in symbols or []}
        if "application/json" in content_type:
            return self._parse_json(response.json(), allowed)
        return self._parse_html(response.text, allowed)

    def _parse_json(self, payload: object, allowed: set[str]) -> list[MarketChameleonIdea]:
        rows = payload if isinstance(payload, list) else getattr(payload, "get", lambda _key, _default=None: [])("ideas", [])
        ideas = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
            if not symbol or (allowed and symbol not in allowed):
                continue
            expiration = self._parse_date(row.get("expiration") or row.get("expiration_date"))
            ideas.append(
                MarketChameleonIdea(
                    symbol=symbol,
                    strategy=str(row.get("strategy") or row.get("tradeType") or "featured idea"),
                    expiration=expiration,
                    title=str(row.get("title") or row.get("name") or f"{symbol} featured idea"),
                    description=str(row.get("description") or row.get("summary") or ""),
                    url=row.get("url"),
                    confidence=float(row["confidence"]) if row.get("confidence") is not None else None,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        return ideas

    def _parse_html(self, html: str, allowed: set[str]) -> list[MarketChameleonIdea]:
        soup = BeautifulSoup(html, "html.parser")
        ideas: list[MarketChameleonIdea] = []
        for card in soup.select("[data-symbol], .trade-idea, .featured-trade, article, tr"):
            text = " ".join(card.get_text(" ", strip=True).split())
            if not text:
                continue
            symbol = (card.get("data-symbol") or self._first_symbol(text)).upper()
            if not symbol or (allowed and symbol not in allowed):
                continue
            link = card.find("a", href=True)
            ideas.append(
                MarketChameleonIdea(
                    symbol=symbol,
                    strategy=self._infer_strategy(text),
                    expiration=None,
                    title=text[:120],
                    description=text,
                    url=urljoin(self.featured_ideas_url, link["href"]) if link else self.featured_ideas_url,
                    confidence=None,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        return ideas[:25]

    def _first_symbol(self, text: str) -> str:
        for token in text.replace(",", " ").split():
            cleaned = "".join(char for char in token if char.isalpha())
            if 1 <= len(cleaned) <= 5 and cleaned.isupper():
                return cleaned
        return ""

    def _infer_strategy(self, text: str) -> str:
        lowered = text.lower()
        for label in ("bull put", "bear call", "bull call", "bear put", "iron condor", "calendar"):
            if label in lowered:
                return label
        if "put" in lowered and "spread" in lowered:
            return "put spread"
        if "call" in lowered and "spread" in lowered:
            return "call spread"
        return "featured idea"

    def _parse_date(self, value: object) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
