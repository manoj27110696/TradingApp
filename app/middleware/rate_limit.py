from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RateLimitMiddleware:
    """Small in-memory limiter for a single-worker public research API."""

    def __init__(self, app: ASGIApp, requests: int, window_seconds: int) -> None:
        self.app = app
        self.requests = max(1, requests)
        self.window_seconds = max(1, window_seconds)
        self._requests_by_client: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._last_cleanup = monotonic()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_limited(scope):
            await self.app(scope, receive, send)
            return

        now = monotonic()
        client_key = self._client_key(scope)
        with self._lock:
            if now - self._last_cleanup >= self.window_seconds:
                stale_clients = [
                    key
                    for key, values in self._requests_by_client.items()
                    if not values or values[-1] <= now - self.window_seconds
                ]
                for key in stale_clients:
                    self._requests_by_client.pop(key, None)
                self._last_cleanup = now

            timestamps = self._requests_by_client[client_key]
            cutoff = now - self.window_seconds
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= self.requests:
                retry_after = max(1, int(self.window_seconds - (now - timestamps[0])) + 1)
                response = JSONResponse(
                    {"detail": "Public request limit reached. Try again shortly."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
                await response(scope, receive, send)
                return

            timestamps.append(now)
            remaining = max(0, self.requests - len(timestamps))

        async def send_with_limit_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-ratelimit-limit", str(self.requests).encode("ascii")),
                        (b"x-ratelimit-remaining", str(remaining).encode("ascii")),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_limit_headers)

    def _is_limited(self, scope: Scope) -> bool:
        path = scope.get("path", "")
        if path == "/api/health":
            return False

        # FastAPI-MCP invokes API routes internally through this synthetic host.
        if Headers(scope=scope).get("host", "").split(":", 1)[0] == "apiserver":
            return False

        return path.startswith(("/api/", "/mcp", "/sse"))

    def _client_key(self, scope: Scope) -> str:
        client = scope.get("client")
        return str(client[0]) if client else "unknown"
