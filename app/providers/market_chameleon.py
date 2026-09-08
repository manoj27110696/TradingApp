from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import unquote, urljoin
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from app.models import MarketChameleonIdea
from app.providers.base import FeaturedIdeasProvider


DESCRIPTION_LIMIT = 600
SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z])?$")
EXPLICIT_SYMBOL_PATTERNS = (
    re.compile(r"\$(?P<symbol>[A-Z]{1,5}(?:\.[A-Z])?)\b"),
    re.compile(r"\b(?:NASDAQ|NYSE|AMEX|ARCA|OTC)\s*:\s*(?P<symbol>[A-Z]{1,5}(?:\.[A-Z])?)\b", re.I),
    re.compile(r"\b(?:ticker|symbol)\s*(?:is|:|=)\s*(?P<symbol>[A-Z]{1,5}(?:\.[A-Z])?)\b", re.I),
    re.compile(r"\((?P<symbol>[A-Z]{1,5}(?:\.[A-Z])?)\)"),
)
URL_SYMBOL_PATTERNS = (
    re.compile(r"/(?:Overview|Quote|Stock)/(?P<symbol>[A-Z]{1,5}(?:\.[A-Z])?)(?:[/?#]|$)", re.I),
    re.compile(r"[?&](?:symbol|ticker)=(?P<symbol>[A-Z]{1,5}(?:\.[A-Z])?)(?:[&#]|$)", re.I),
)


class MarketChameleonFeaturedIdeasProvider(FeaturedIdeasProvider):
    def __init__(
        self,
        featured_ideas_url: str,
        session_cookie: str = "",
        max_age_days: int = 7,
    ) -> None:
        self.featured_ideas_url = featured_ideas_url
        self.session_cookie = session_cookie
        self.max_age_days = max(1, max_age_days)

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
            symbol = str(row.get("symbol") or row.get("ticker") or "").upper().strip()
            published_at = self._parse_datetime(
                row.get("published_at") or row.get("publishedAt") or row.get("published") or row.get("date")
            )
            if not self._valid_symbol(symbol, allowed) or self._is_stale(published_at):
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
                    published_at=published_at,
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
            link = card.find("a", href=True)
            url = urljoin(self.featured_ideas_url, link["href"]) if link else self.featured_ideas_url
            symbol = str(card.get("data-symbol") or self._explicit_symbol(text, allowed, [url])).upper()
            time_node = card.find("time")
            published_at = self._parse_datetime(
                time_node.get("datetime") if time_node and time_node.get("datetime") else time_node.get_text(strip=True) if time_node else None
            )
            if not self._valid_symbol(symbol, allowed) or self._is_stale(published_at):
                continue
            ideas.append(
                MarketChameleonIdea(
                    symbol=symbol,
                    strategy=self._infer_strategy(text),
                    expiration=None,
                    title=text[:120],
                    description=self._clean_description(text),
                    url=url,
                    confidence=None,
                    published_at=published_at,
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
                or self._xml_text(item, "encoded")
            )
            text = " ".join(f"{title} {description}".split())
            url = self._feed_link(item)
            symbol = self._explicit_symbol(text, allowed, [url])
            published_at = self._parse_datetime(
                self._xml_text(item, "pubDate")
                or self._xml_text(item, "published")
                or self._xml_text(item, "updated")
                or self._xml_text(item, "date")
            )
            if not self._valid_symbol(symbol, allowed) or self._is_stale(published_at):
                continue
            ideas.append(
                MarketChameleonIdea(
                    symbol=symbol,
                    strategy=self._infer_strategy(text),
                    expiration=None,
                    title=title or text[:120] or f"{symbol} RSS idea",
                    description=self._clean_description(description or text),
                    url=url,
                    confidence=None,
                    published_at=published_at,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        return ideas[:25]

    def _looks_like_feed(self, text: str) -> bool:
        stripped = text.lstrip()[:200].lower()
        return stripped.startswith("<?xml") or stripped.startswith("<rss") or stripped.startswith("<feed")

    def _xml_text(self, item: ElementTree.Element, tag: str) -> str:
        for node in item.iter():
            local_name = str(node.tag).rsplit("}", 1)[-1]
            if local_name == tag:
                return " ".join((node.text or "").split())
        return ""

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

    def _explicit_symbol(self, text: str, allowed: set[str], urls: list[str]) -> str:
        decoded_urls = [unquote(url) for url in urls if url]
        for url in decoded_urls:
            for pattern in URL_SYMBOL_PATTERNS:
                match = pattern.search(url)
                if match and self._valid_symbol(match.group("symbol"), allowed):
                    return match.group("symbol").upper()

        for pattern in EXPLICIT_SYMBOL_PATTERNS:
            for match in pattern.finditer(text):
                symbol = match.group("symbol").upper()
                if self._valid_symbol(symbol, allowed):
                    return symbol

        # A caller-provided universe is authoritative and avoids guessing from prose.
        for symbol in sorted(allowed, key=len, reverse=True):
            if re.search(rf"(?<![A-Z0-9.]){re.escape(symbol)}(?![A-Z0-9.])", text, re.I):
                return symbol
        return ""

    def _valid_symbol(self, symbol: str, allowed: set[str]) -> bool:
        normalized = symbol.upper().strip()
        return bool(SYMBOL_PATTERN.fullmatch(normalized)) and (not allowed or normalized in allowed)

    def _parse_datetime(self, value: object) -> datetime | None:
        if not value:
            return None
        raw = str(value).strip()
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _is_stale(self, published_at: datetime | None) -> bool:
        if published_at is None:
            return False
        now = datetime.now(timezone.utc)
        return published_at < now - timedelta(days=self.max_age_days) or published_at > now + timedelta(days=1)

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
