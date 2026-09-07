from datetime import date, datetime, timezone
import asyncio
from copy import deepcopy
from threading import Lock
from time import monotonic
from urllib.parse import urljoin

import httpx

from app.models import OptionChain, OptionContract, OptionType
from app.providers.base import OptionChainProvider


class CuteMarketsOptionChainProvider(OptionChainProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        strike_window_pct: float = 0.12,
        request_timeout_seconds: float = 8.0,
        max_expiration_pages: int = 2,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.strike_window_pct = max(0.03, strike_window_pct)
        self.request_timeout_seconds = max(1.0, request_timeout_seconds)
        self.max_expiration_pages = max(1, max_expiration_pages)
        self.cache_ttl_seconds = max(1, cache_ttl_seconds)
        self._cache: dict[str, tuple[float, object]] = {}
        self._cache_lock = Lock()

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def expirations(self, symbol: str) -> list[date]:
        symbol = symbol.upper()
        cache_key = f"expirations:{symbol}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        dates: list[date] = []
        next_url: str | None = f"/v1/tickers/expirations/{symbol}/"
        for _ in range(self.max_expiration_pages):
            if not next_url:
                break
            payload = await self._get(next_url)
            for item in payload.get("results", []):
                value = item
                if isinstance(item, dict):
                    value = item.get("expiration_date") or item.get("date")
                if isinstance(value, str):
                    dates.append(date.fromisoformat(value))
            next_url = payload.get("next_url")
        result = sorted(set(dates))
        self._cache_put(cache_key, result)
        return result

    async def chain(self, symbol: str, expiration: date) -> OptionChain:
        symbol = symbol.upper()
        cache_key = f"chain:{symbol}:{expiration.isoformat()}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        probe = await self._chain_page(symbol, expiration, limit=1)
        probe_results = probe.get("results", [])
        underlying_price = self._underlying_price(probe_results)

        params: dict[str, str | int | float] = {
            "expiration_date": expiration.isoformat(),
            "limit": 100,
            "sort": "strike_price",
            "order": "asc",
        }
        if underlying_price > 0:
            half_width = max(10.0, underlying_price * self.strike_window_pct)
            params["strike_price.gte"] = round(max(0.01, underlying_price - half_width), 2)
            params["strike_price.lte"] = round(underlying_price + half_width, 2)

        call_payload, put_payload = await asyncio.gather(
            self._chain_page(symbol, expiration, contract_type="call", **params),
            self._chain_page(symbol, expiration, contract_type="put", **params),
        )
        contracts = [
            self._contract(symbol, item)
            for payload in (call_payload, put_payload)
            for item in payload.get("results", [])
        ]

        if contracts and underlying_price <= 0:
            underlying_price = self._underlying_price_from_contracts(contracts)

        result = OptionChain(
            symbol=symbol,
            underlying_price=underlying_price,
            expiration=expiration,
            fetched_at=datetime.now(timezone.utc),
            contracts=contracts,
            source="cutemarkets",
        )
        self._cache_put(cache_key, result)
        return result

    async def _chain_page(self, symbol: str, expiration: date, **params) -> dict:
        params.setdefault("expiration_date", expiration.isoformat())
        return await self._get(f"/v1/options/chain/{symbol.upper()}/", params=params)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = path if path.startswith("http") else urljoin(f"{self.base_url}/", path.lstrip("/"))
        timeout = httpx.Timeout(self.request_timeout_seconds, connect=min(5.0, self.request_timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(2):
                response = await client.get(url, params=params, headers=self.headers)
                if response.status_code not in (429, 500, 502, 503, 504):
                    response.raise_for_status()
                    break
                if attempt == 1:
                    response.raise_for_status()
                await asyncio.sleep(0.6 * (attempt + 1))
        payload = response.json()
        if payload.get("status") not in (None, "OK"):
            raise ValueError(f"CuteMarkets returned status {payload.get('status')}")
        return payload

    def _cache_get(self, key: str):
        now = monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            expires_at, value = cached
            if expires_at <= now:
                self._cache.pop(key, None)
                return None
            return deepcopy(value)

    def _cache_put(self, key: str, value: object) -> None:
        with self._cache_lock:
            self._cache[key] = (monotonic() + self.cache_ttl_seconds, deepcopy(value))

    def _contract(self, underlying: str, item: dict) -> OptionContract:
        details = item.get("details") or {}
        greeks = item.get("greeks") or {}
        quote = item.get("last_quote") or {}
        trade = item.get("last_trade") or {}
        day = item.get("day") or {}

        option_type = OptionType.call if details.get("contract_type") == "call" else OptionType.put
        last = self._number(trade.get("price") or day.get("close") or item.get("fmv"))
        bid = self._number(quote.get("bid"))
        ask = self._number(quote.get("ask"))
        if bid <= 0 and ask <= 0 and last is not None:
            bid = last
            ask = last

        return OptionContract(
            symbol=str(details.get("ticker") or ""),
            underlying=underlying,
            expiration=date.fromisoformat(details["expiration_date"]),
            strike=float(details["strike_price"]),
            option_type=option_type,
            bid=bid,
            ask=ask,
            last=last,
            volume=self._int(day.get("volume") or trade.get("size")),
            open_interest=self._int(item.get("open_interest")),
            delta=self._number_or_none(greeks.get("delta")),
            gamma=self._number_or_none(greeks.get("gamma")),
            theta=self._number_or_none(greeks.get("theta")),
            vega=self._number_or_none(greeks.get("vega")),
            implied_volatility=self._number_or_none(item.get("implied_volatility")),
        )

    def _underlying_price(self, results: list[dict]) -> float:
        for item in results:
            price = self._number((item.get("underlying_asset") or {}).get("price"))
            if price > 0:
                return price
        return 0.0

    def _underlying_price_from_contracts(self, contracts: list[OptionContract]) -> float:
        strikes = sorted(contract.strike for contract in contracts)
        return strikes[len(strikes) // 2] if strikes else 0.0

    def _number(self, value) -> float:
        number = self._number_or_none(value)
        return number if number is not None else 0.0

    def _number_or_none(self, value) -> float | None:
        if value in (None, ""):
            return None
        return float(value)

    def _int(self, value) -> int | None:
        if value in (None, ""):
            return None
        return int(value)
