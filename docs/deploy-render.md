# Deploy to Render for a Custom GPT

This is the lowest-friction free deployment path for the Options Spread Copilot.

## 1. Push the GitHub Repo

Use `https://github.com/manoj27110696/TradingApp`.

The repo includes `render.yaml`, so Render can detect the web service settings automatically.

## 2. Create the Render Service

1. Go to Render and choose **New +**.
2. Choose **Blueprint** if Render detects `render.yaml`, or choose **Web Service** manually.
3. Connect `manoj27110696/TradingApp`.
4. Use these settings if entering them manually:

```text
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
Plan: Free
```

## 3. Add Environment Variables

Set these in Render:

```text
APP_ENV=production
SCAN_TIMEOUT_SECONDS=45
MAX_SCAN_SYMBOLS=5
SCAN_CONCURRENCY=3
MARKET_CHAMELEON_FEATURED_IDEAS_URL=<licensed JSON, RSS/Atom, HTML, or export URL>
MARKET_CHAMELEON_SESSION_COOKIE=<only if your licensed feed requires it>
PUBLIC_RATE_LIMIT_REQUESTS=60
PUBLIC_RATE_LIMIT_WINDOW_SECONDS=60
DEFAULT_SYMBOLS=SPY,QQQ,IWM,AAPL,MSFT,NVDA,TSLA
```

No option-chain provider is currently bundled. Remove any old `CUTEMARKETS_*` variables from the Render service; they are no longer read by the application.

## 4. Smoke Test

After deployment, open:

```text
https://options-spread-copilot.onrender.com/api/health
```

You should see:

```json
{"status":"ok"}
```

Then test the featured-ideas endpoint:

```powershell
Invoke-RestMethod `
  -Uri "https://options-spread-copilot.onrender.com/api/market-chameleon/ideas?symbols=SPY,QQQ&limit=3"
```

## 5. Update the Custom GPT Action Schema

The checked-in schema already points to:

```text
https://options-spread-copilot.onrender.com
```

For Claude or another MCP client, use the authentication-free Streamable HTTP endpoint:

```text
https://options-spread-copilot.onrender.com/mcp
```

The compatibility SSE endpoint is `https://options-spread-copilot.onrender.com/sse`.

## 6. Create the Custom GPT Action

1. Open ChatGPT and create or edit your GPT.
2. Go to **Actions**.
3. Import `custom_gpt/action_openapi.yaml`.
4. Set authentication to **None**.
5. Paste the instructions from `docs/custom-gpt.md` into the GPT instructions.

## 7. Ask It

Try:

```text
Give me the best SPY and QQQ spreads expiring next week.
```

Until a replacement option-chain provider is added, the GPT can return configured featured ideas but cannot rank spreads from live chain data.

## Notes

- Free services may sleep, so the first request can be slow.
- Do not put broker credentials in the GPT.
- This app ranks research candidates only. It does not place trades.
