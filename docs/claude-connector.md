# Claude Connector Setup

The app exposes an authentication-free MCP server at:

```text
https://options-spread-copilot.onrender.com/mcp
```

This is the preferred Streamable HTTP endpoint. A legacy SSE endpoint remains available at `/sse` for older clients.

## Connect or Refresh Claude

1. Open Claude Settings and select **Connectors**.
2. Remove the existing **Options Copilot** connector if it was created before authentication was removed.
3. Add a custom connector named **Options Copilot** using the `/mcp` URL above.
4. Do not enter an API key or OAuth credentials.
5. Enable the connector in the chat's tools menu and start a new chat before rerunning the routine.

Claude can retain the authentication state associated with an existing connector. Removing and re-adding it ensures Claude discovers the current authentication-free MCP endpoint.

## Smoke Test

Ask Claude:

```text
Use Options Copilot to list the available option expirations for SPY.
```

The connector should expose these tools:

- `getMarketChameleonIdeas`
- `getSpreadRecommendations`

If a client does not support Streamable HTTP, configure it with `https://options-spread-copilot.onrender.com/sse` instead.
