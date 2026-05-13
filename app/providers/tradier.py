from datetime import date, datetime, timezone

import httpx

from app.models import OptionChain, OptionContract, OptionType
from app.providers.base import OptionChainProvider


class TradierOptionChainProvider(OptionChainProvider):
    def __init__(self, token: str, base_url: str) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    async def expirations(self, symbol: str) -> list[date]:
        url = f"{self.base_url}/markets/options/expirations"
        params = {"symbol": symbol.upper(), "includeAllRoots": "true", "strikes": "false"}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, params=params, headers=self.headers)
            response.raise_for_status()
        payload = response.json()
        expirations = payload.get("expirations", {}).get("date", [])
        if isinstance(expirations, str):
            expirations = [expirations]
        return [date.fromisoformat(item) for item in expirations]

    async def chain(self, symbol: str, expiration: date) -> OptionChain:
        symbol = symbol.upper()
        quote_url = f"{self.base_url}/markets/quotes"
        chain_url = f"{self.base_url}/markets/options/chains"
        async with httpx.AsyncClient(timeout=30) as client:
            quote_response = await client.get(quote_url, params={"symbols": symbol}, headers=self.headers)
            quote_response.raise_for_status()
            chain_response = await client.get(
                chain_url,
                params={"symbol": symbol, "expiration": expiration.isoformat(), "greeks": "true"},
                headers=self.headers,
            )
            chain_response.raise_for_status()

        quote = quote_response.json().get("quotes", {}).get("quote", {})
        if isinstance(quote, list):
            quote = quote[0] if quote else {}
        underlying_price = float(quote.get("last") or quote.get("close") or 0)

        raw_options = chain_response.json().get("options", {}).get("option", [])
        if isinstance(raw_options, dict):
            raw_options = [raw_options]

        contracts = []
        for item in raw_options:
            greeks = item.get("greeks") or {}
            option_type = OptionType.call if item.get("option_type") == "call" else OptionType.put
            contracts.append(
                OptionContract(
                    symbol=item["symbol"],
                    underlying=symbol,
                    expiration=expiration,
                    strike=float(item["strike"]),
                    option_type=option_type,
                    bid=float(item.get("bid") or 0),
                    ask=float(item.get("ask") or 0),
                    last=float(item["last"]) if item.get("last") is not None else None,
                    volume=int(item["volume"]) if item.get("volume") is not None else None,
                    open_interest=int(item["open_interest"]) if item.get("open_interest") is not None else None,
                    delta=float(greeks["delta"]) if greeks.get("delta") is not None else None,
                    gamma=float(greeks["gamma"]) if greeks.get("gamma") is not None else None,
                    theta=float(greeks["theta"]) if greeks.get("theta") is not None else None,
                    vega=float(greeks["vega"]) if greeks.get("vega") is not None else None,
                    implied_volatility=float(greeks["mid_iv"]) if greeks.get("mid_iv") is not None else None,
                )
            )

        return OptionChain(
            symbol=symbol,
            underlying_price=underlying_price,
            expiration=expiration,
            fetched_at=datetime.now(timezone.utc),
            contracts=contracts,
            source="tradier",
        )
