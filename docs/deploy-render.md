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
APP_API_KEY=<generate a long random secret>
CUTEMARKETS_API_KEY=<your CuteMarkets key for delayed options data>
CUTEMARKETS_BASE_URL=https://api.cutemarkets.com
CUTEMARKETS_CHAIN_STRIKE_WINDOW_PCT=0.12
MARKET_CHAMELEON_FEATURED_IDEAS_URL=<licensed feed or export URL>
MARKET_CHAMELEON_SESSION_COOKIE=<only if your licensed feed requires it>
DEFAULT_SYMBOLS=SPY,QQQ,IWM,AAPL,MSFT,NVDA,TSLA
```

Set `CUTEMARKETS_API_KEY` for the free delayed-data path. Leave it empty for the first smoke test if you want to confirm deployment with sample data.

## 4. Smoke Test

After deployment, open:

```text
https://options-spread-copilot.onrender.com/api/health
```

You should see:

```json
{"status":"ok"}
```

Then test a protected endpoint with the `X-API-Key` header:

```powershell
Invoke-RestMethod `
  -Uri "https://options-spread-copilot.onrender.com/api/spreads/recommendations?symbols=SPY,QQQ&window=next_week&limit=3" `
  -Headers @{ "X-API-Key" = "<your APP_API_KEY>" }
```

## 5. Update the Custom GPT Action Schema

The checked-in schema already points to:

```text
https://options-spread-copilot.onrender.com
```

## 6. Create the Custom GPT Action

1. Open ChatGPT and create or edit your GPT.
2. Go to **Actions**.
3. Import `custom_gpt/action_openapi.yaml`.
4. Set authentication to **API Key**.
5. Use header name `X-API-Key`.
6. Paste the same value you set for `APP_API_KEY` in Render.
7. Paste the instructions from `docs/custom-gpt.md` into the GPT instructions.

## 7. Ask It

Try:

```text
Give me the best SPY and QQQ spreads expiring next week.
```

The GPT should call `getSpreadRecommendations`, summarize candidates, and remind you to verify live quotes and risk.

## Notes

- Free services may sleep, so the first request can be slow.
- Do not put broker credentials in the GPT.
- This app ranks research candidates only. It does not place trades.
