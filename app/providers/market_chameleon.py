from datetime import date, datetime, timezone
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from app.models import MarketChameleonIdea
from app.providers.base import FeaturedIdeasProvider


DESCRIPTION_LIMIT = 600


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
        if "xml" in content_type or self._looks_like_feed(response.text):
            return self._parse_feed(response.text, allowed)
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
                    description=self._clean_description(str(row.get("description") or row.get("summary") or "")),
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
                    description=self._clean_description(text),
                    url=urljoin(self.featured_ideas_url, link["href"]) if link else self.featured_ideas_url,
                    confidence=None,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        return ideas[:25]

    def _parse_feed(self, xml: str, allowed: set[str]) -> list[MarketChameleonIdea]:
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            return []

        ideas: list[MarketChameleonIdea] = []
        for item in list(root.findall(".//item")) + list(root.findall(".//{http://www.w3.org/2005/Atom}entry")):
            title = self._xml_text(item, "title")
            description = (
                self._xml_text(item, "description")
                or self._xml_text(item, "summary")
                or self._xml_text(item, "content")
            )
            text = " ".join(f"{title} {description}".split())
            symbol = self._first_symbol(text)
            if not symbol or (allowed and symbol not in allowed):
                continue
            ideas.append(
                MarketChameleonIdea(
                    symbol=symbol,
                    strategy=self._infer_strategy(text),
                    expiration=None,
                    title=title or text[:120] or f"{symbol} RSS idea",
                    description=self._clean_description(description or text),
                    url=self._feed_link(item),
                    confidence=None,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        return ideas[:25]

    def _looks_like_feed(self, text: str) -> bool:
        stripped = text.lstrip()[:200].lower()
        return stripped.startswith("<?xml") or stripped.startswith("<rss") or stripped.startswith("<feed")

    def _xml_text(self, item: ElementTree.Element, tag: str) -> str:
        node = item.find(tag)
        if node is None:
            node = item.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
        return " ".join((node.text or "").split()) if node is not None else ""

    def _feed_link(self, item: ElementTree.Element) -> str:
        link = item.find("link")
        if link is not None:
            if link.text:
                return link.text.strip()
            href = link.get("href")
            if href:
                return href
        atom_link = item.find("{http://www.w3.org/2005/Atom}link")
        if atom_link is not None and atom_link.get("href"):
            return atom_link.get("href", "")
        return self.featured_ideas_url

    def _clean_description(self, value: str) -> str:
        soup = BeautifulSoup(value, "html.parser")
        for tag in soup.find_all(["img", "script", "style"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ", strip=True).split())
        return text[:DESCRIPTION_LIMIT]

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
        if "credit put" in lowered:
            return "bull put"
        if "credit call" in lowered:
            return "bear call"
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
