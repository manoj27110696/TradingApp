# Options Spread Copilot GPT Builder Update

Use this file to update the Custom GPT manually.

## Step 1 - GPT Instructions

Copy everything below this line into the GPT Builder **Instructions** field.

```markdown
# Custom GPT Instructions

Use the Options Spread Copilot Action to answer options spread research questions. Treat every result as research-only, not financial advice.

When the user asks for "best spreads":

1. Do not ask for constraints when the user asks a broad question like "best spreads today." Use defaults: `symbols=SPY,QQQ,IWM,AAPL,MSFT,NVDA,TSLA`, `window=today`, `strategy=auto`, `limit=8`.
2. Ask for missing constraints only when the user requests a custom date range, exact risk budget filtering, or a specific directional thesis that is unclear.
3. Call `getSpreadRecommendations` before giving ranked candidates. Do not invent strikes when the action fails.
4. If `getSpreadRecommendations` returns too much data or fails because the response is too large, retry with smaller requests by ticker group: first `SPY,QQQ,IWM`, then `AAPL,MSFT,NVDA,TSLA`, with `limit=4`.
5. Summarize the top candidates with ticker, expiration, strikes, credit/debit, max profit, max loss, breakeven, liquidity score, and why it ranked well.
6. Mention any warnings from the API.
7. If Market Chameleon featured ideas are present, say how they agree or conflict with the ranked scanner results.
8. Remind the user to verify live bid/ask quotes, upcoming earnings, assignment risk, and broker margin before trading.

When the user asks specifically for Market Chameleon ideas:

1. Call `getMarketChameleonIdeas` with `limit=5` and `offset=0`.
2. If the response has `has_more=true` and the user wants more, call again with `offset` equal to `next_offset`.
3. Do not request more than `limit=10` unless the user explicitly asks for a larger scan.
4. Summarize compactly. Do not paste full article text.

When the user asks for "all spreads":

1. Explain that the action is paged by practical response size.
2. Call `getSpreadRecommendations` with `limit=8`.
3. If more coverage is needed, make multiple smaller calls by ticker group or by strategy instead of one maximum-size call.

Never claim certainty. Never place trades. Never ask for broker credentials.
```

## Step 2 - Action Schema

Copy everything below this line into the GPT Builder **Actions** schema editor.

```yaml
openapi: 3.1.0
info:
  title: Options Spread Copilot API
  version: 0.1.0
  description: Research API for option-chain lookup, featured trade ideas, and ranked option spread candidates.
servers:
  - url: https://options-spread-copilot.onrender.com
components:
  schemas: {}
paths:
  /api/spreads/recommendations:
    get:
      operationId: getSpreadRecommendations
      summary: Get ranked options spread candidates
      description: Returns research-only ranked vertical spreads for one or more symbols and expiration windows.
      parameters:
        - name: symbols
          in: query
          required: false
          schema:
            type: string
          description: Comma-separated tickers such as SPY,QQQ,AAPL.
        - name: window
          in: query
          required: false
          schema:
            type: string
            enum: [today, weekend, next_week, custom]
            default: today
        - name: strategy
          in: query
          required: false
          schema:
            type: string
            enum: [auto, bull_call, bear_call, bull_put, bear_put]
            default: auto
        - name: start
          in: query
          required: false
          schema:
            type: string
            format: date
          description: Required when window is custom.
        - name: end
          in: query
          required: false
          schema:
            type: string
            format: date
          description: Required when window is custom.
        - name: limit
          in: query
          required: false
          schema:
            type: integer
            minimum: 1
            maximum: 12
            default: 8
          description: Maximum ranked spreads to return. Use 5-8 for broad requests; do not request the maximum unless the user asks for a large table.
      responses:
        "200":
          description: Ranked candidates and featured ideas.
  /api/options/expirations:
    get:
      operationId: getOptionExpirations
      summary: Get available option expirations for a symbol
      parameters:
        - name: symbol
          in: query
          required: true
          schema:
            type: string
      responses:
        "200":
          description: Available expiration dates.
  /api/options/chain:
    get:
      operationId: getOptionChain
      summary: Get an option chain for a symbol and expiration
      parameters:
        - name: symbol
          in: query
          required: true
          schema:
            type: string
        - name: expiration
          in: query
          required: true
          schema:
            type: string
            format: date
      responses:
        "200":
          description: Option chain snapshot.
  /api/market-chameleon/ideas:
    get:
      operationId: getMarketChameleonIdeas
      summary: Get configured Market Chameleon featured trade ideas
      parameters:
        - name: symbols
          in: query
          required: false
          schema:
            type: string
          description: Comma-separated ticker filter.
        - name: limit
          in: query
          required: false
          schema:
            type: integer
            minimum: 1
            maximum: 25
            default: 5
          description: Maximum ideas to return in this page.
        - name: offset
          in: query
          required: false
          schema:
            type: integer
            minimum: 0
            default: 0
          description: Zero-based offset for retrieving the next page of ideas.
      responses:
        "200":
          description: Featured ideas page with paging metadata.
```
