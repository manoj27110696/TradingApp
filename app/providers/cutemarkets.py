from datetime import date, datetime, timezone
from urllib.parse import urljoin

import httpx

from app.models import OptionChain, OptionContract, OptionType
from app.providers.base import OptionChainProvider


class CuteMarketsOptionChainProvider(OptionChainProvider):
    def __init__(self, api_key: str, base_url: str, strike_window_pct: float = 0.12) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.strike_window_pct = max(0.03, strike_window_pct)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def expirations(self, symbol: str) -> list[date]:
        payload = await self._get(f"/v1/tickers/expirations/{symbol.upper()}/")
        raw_expirations = payload.get("results", [])
        dates: list[date] = []
        for item in raw_expirations:
            value = item
            if isinstance(item, dict):
                value = item.get("expiration_date") or item.get("date")
            if isinstance(value, str):
                dates.append(date.fromisoformat(value))
        return sorted(set(dates))

    async def chain(self, symbol: str, expiration: date) -> OptionChain:
        symbol = symbol.upper()
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

        contracts: list[OptionContract] = []
        for option_type in ("call", "put"):
            payload = await self._chain_page(symbol, expiration, contract_type=option_type, **params)
            contracts.extend(self._contract(symbol, item) for item in payload.get("results", []))

        if contracts and underlying_price <= 0:
            underlying_price = self._underlying_price_from_contracts(contracts)

        return OptionChain(
            symbol=symbol,
            underlying_price=underlying_price,
            expiration=expiration,
            fetched_at=datetime.now(timezone.utc),
            contracts=contracts,
            source="cutemarkets",
        )

    async def _chain_page(self, symbol: str, expiration: date, **params) -> dict:
        params.setdefault("expiration_date", expiration.isoformat())
        return await self._get(f"/v1/options/chain/{symbol.upper()}/", params=params)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = urljoin(f"{self.base_url}/", path.lstrip("/"))
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params, headers=self.headers)
            response.raise_for_status()
        payload = response.json()
        if payload.get("status") not in (None, "OK"):
            raise ValueError(f"CuteMarkets returned status {payload.get('status')}")
        return payload

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
