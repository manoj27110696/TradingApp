# Options Spread Copilot

A local options research API and dashboard that can be connected to ChatGPT as a Custom GPT Action. It pulls option-chain data from a provider, scores common vertical spreads, and exposes concise endpoints for questions like:

- "Give me the best spreads for today."
- "Find spreads expiring this weekend."
- "Compare next week's SPY and QQQ spreads."
- "Blend in Market Chameleon featured trade ideas."

This project is for research and paper-trading workflow support only. It does not place orders and it is not financial advice.

## Features

- FastAPI backend with OpenAPI docs
- CuteMarkets delayed option-chain adapter for free/cheap research data
- Market Chameleon featured-ideas ingest hook
- Vertical spread scanner for bull call, bear call, bull put, and bear put spreads
- Expiration windows: today, this weekend, next week, or custom ISO date range
- Custom GPT Action schema at `custom_gpt/action_openapi.yaml`
- Browser dashboard at `/`

## Quick Start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
python -m uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`.

## Configuration

Set these in `.env`:

```text
CUTEMARKETS_API_KEY=
CUTEMARKETS_BASE_URL=https://api.cutemarkets.com
CUTEMARKETS_CHAIN_STRIKE_WINDOW_PCT=0.12
MARKET_CHAMELEON_FEATURED_IDEAS_URL=
MARKET_CHAMELEON_SESSION_COOKIE=
```

`MARKET_CHAMELEON_FEATURED_IDEAS_URL` can point to a licensed JSON feed, an RSS/Atom feed, or a Market Chameleon HTML page. RSS/blog feeds are treated as research ideas and parsed for ticker/strategy text; the app does not scrape around Market Chameleon access controls.

Set `CUTEMARKETS_API_KEY` to enable option-chain data. The app does not use sample market data as a fallback; if no provider is configured, data endpoints return a configuration error. The CuteMarkets provider fetches a near-the-money slice of each chain to avoid burning through free-plan limits.

## Custom GPT Setup

1. Deploy this API somewhere ChatGPT can reach over HTTPS.
2. In ChatGPT, create a GPT and add an Action.
3. Import `custom_gpt/action_openapi.yaml`.
4. Set authentication to match your deployment, usually an API key header in front of this service.
5. Paste the instructions from `docs/custom-gpt.md` into the GPT instructions.

## Suggested Data Providers

- CuteMarkets: free 15-minute delayed option-chain research data.
- Polygon, ThetaData, ORATS, Cboe LiveVol, or Interactive Brokers can be added behind `OptionChainProvider`.
- Market Chameleon featured ideas can be wired through a paid/export feed or a private page endpoint you are licensed to access.

## Risk Notes

- Scores are ranking heuristics, not trade recommendations.
- Liquidity, bid/ask width, earnings, assignment risk, hard-to-borrow risk, and broker margin rules should be reviewed manually.
- Do not expose broker credentials to a Custom GPT Action.
