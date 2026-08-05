"""Self-hosted OAuth 2.1 authorization server for MCP connector auth.

This app is single-tenant: the only "user" is whoever holds APP_API_KEY.
Remote MCP clients (e.g. Claude's Custom Connectors) can't be handed that
key directly — the MCP Authorization spec expects a client to discover an
authorization server, register itself (RFC 7591), and complete an
authorization-code + PKCE flow (RFC 6749 / RFC 7636). This module
implements exactly that, gating the interactive login step behind
APP_API_KEY instead of a real user database.

Issued codes and tokens are kept in memory. That's fine for a single
personal-use instance: state resets on redeploy, which just means
connected clients re-authorize.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import Settings, get_settings

AUTH_CODE_TTL_SECONDS = 5 * 60
ACCESS_TOKEN_TTL_SECONDS = 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

router = APIRouter(tags=["oauth"], include_in_schema=False)
bearer_scheme = HTTPBearer(auto_error=False)


class _RegisteredClient(BaseModel):
    client_id: str
    redirect_uris: list[str]
    client_name: str


class _AuthCode(BaseModel):
    client_id: str
    redirect_uri: str
    code_challenge: str
    expires_at: float


class _Token(BaseModel):
    expires_at: float


_clients: dict[str, _RegisteredClient] = {}
_auth_codes: dict[str, _AuthCode] = {}
_access_tokens: dict[str, _Token] = {}
_refresh_tokens: dict[str, _Token] = {}


def _prune_expired(store: dict[str, _Token] | dict[str, _AuthCode]) -> None:
    now = time.time()
    for key in [key for key, value in store.items() if value.expires_at < now]:
        del store[key]


def _issue_access_token() -> str:
    token = secrets.token_urlsafe(32)
    _access_tokens[token] = _Token(expires_at=time.time() + ACCESS_TOKEN_TTL_SECONDS)
    return token


def _public_base_url(request: Request) -> str:
    """Resolve the externally visible base URL.

    Prefers X-Forwarded-Proto/Host over request.base_url: behind Render's (or
    any) TLS-terminating proxy, uvicorn only sees plain HTTP internally unless
    it's explicitly told to trust the proxy's forwarded headers. If that trust
    isn't configured, request.base_url silently resolves to http://, which
    makes every OAuth discovery URL insecure and OAuth clients refuse to
    register against it.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto.split(',')[0].strip()}://{forwarded_host.split(',')[0].strip()}"
    return str(request.base_url).rstrip("/")


async def require_bearer_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
    bearer_token: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    """Accept the static APP_API_KEY or a token minted by this OAuth server.

    Raising 401 with a `resource_metadata` WWW-Authenticate hint is what
    prompts an MCP client to discover /.well-known/oauth-protected-resource
    and start the OAuth flow, so this must be a real 401, not a silent skip.
    """
    if not settings.app_api_key:
        return  # Auth disabled — no APP_API_KEY configured
    token = bearer_token.credentials if bearer_token else None
    if token:
        _prune_expired(_access_tokens)
        if token == settings.app_api_key or token in _access_tokens:
            return
    base = _public_base_url(request)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid bearer token.",
        headers={
            "WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"',
        },
    )


# ---------------------------------------------------------------------------
# RFC 8414 / RFC 9728 — discovery metadata
# ---------------------------------------------------------------------------


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request) -> dict[str, object]:
    base = _public_base_url(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
    }


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request: Request) -> dict[str, object]:
    base = _public_base_url(request)
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
    }


# ---------------------------------------------------------------------------
# RFC 7591 — Dynamic Client Registration
# ---------------------------------------------------------------------------


class ClientRegistrationRequest(BaseModel):
    redirect_uris: list[str]
    client_name: str | None = None
    grant_types: list[str] | None = None
    token_endpoint_auth_method: str | None = None


class ClientRegistrationResponse(BaseModel):
    client_id: str
    client_id_issued_at: int
    redirect_uris: list[str]
    grant_types: list[str]
    token_endpoint_auth_method: str
    client_name: str


@router.post("/oauth/register", status_code=status.HTTP_201_CREATED)
async def register_client(payload: ClientRegistrationRequest) -> ClientRegistrationResponse:
    if not payload.redirect_uris:
        raise HTTPException(status_code=400, detail="redirect_uris is required.")
    client_id = secrets.token_urlsafe(24)
    client_name = payload.client_name or "MCP client"
    _clients[client_id] = _RegisteredClient(
        client_id=client_id,
        redirect_uris=payload.redirect_uris,
        client_name=client_name,
    )
    return ClientRegistrationResponse(
        client_id=client_id,
        client_id_issued_at=int(time.time()),
        redirect_uris=payload.redirect_uris,
        grant_types=payload.grant_types or ["authorization_code", "refresh_token"],
        token_endpoint_auth_method="none",
        client_name=client_name,
    )


# ---------------------------------------------------------------------------
# Authorization endpoint — single-user login gate + PKCE (RFC 7636)
# ---------------------------------------------------------------------------


def _login_page(*, client_name: str, error: str | None, hidden: dict[str, str]) -> str:
    hidden_inputs = "".join(
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value)}">'
        for name, value in hidden.items()
    )
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    safe_name = html.escape(client_name)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Authorize {safe_name}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 26rem; margin: 12vh auto; padding: 0 1.5rem; color: #111; }}
h1 {{ font-size: 1.15rem; }}
input[type=password] {{ width: 100%; padding: .6rem; font-size: 1rem; box-sizing: border-box; margin: .5rem 0 1rem; }}
button {{ width: 100%; padding: .65rem; font-size: 1rem; cursor: pointer; }}
.error {{ color: #b00020; }}
</style></head>
<body>
<h1>Authorize {safe_name}</h1>
<p>This grants the client access to your Options Spread Copilot tools using your API key.</p>
{error_html}
<form method="post">
{hidden_inputs}
<label for="api_key">API key</label>
<input type="password" id="api_key" name="api_key" autofocus required>
<button type="submit">Authorize</button>
</form>
</body></html>"""


def _validate_authorize_params(
    *,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str | None,
    code_challenge_method: str | None,
) -> _RegisteredClient:
    if response_type != "code":
        raise HTTPException(status_code=400, detail="Only response_type=code is supported.")
    client = _clients.get(client_id)
    if client is None:
        raise HTTPException(status_code=400, detail="Unknown client_id. Register via /oauth/register first.")
    if redirect_uri not in client.redirect_uris:
        raise HTTPException(status_code=400, detail="redirect_uri does not match a registered redirect URI.")
    if not code_challenge or code_challenge_method != "S256":
        raise HTTPException(status_code=400, detail="PKCE with code_challenge_method=S256 is required.")
    return client


def _hidden_fields(
    *,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str | None,
    code_challenge_method: str | None,
    state: str | None,
    scope: str | None,
) -> dict[str, str]:
    hidden = {
        "response_type": response_type,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge or "",
        "code_challenge_method": code_challenge_method or "",
    }
    if state is not None:
        hidden["state"] = state
    if scope is not None:
        hidden["scope"] = scope
    return hidden


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize(
    response_type: str = Query("code"),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: str | None = Query(None),
    code_challenge: str | None = Query(None),
    code_challenge_method: str | None = Query(None),
    scope: str | None = Query(None),
) -> HTMLResponse:
    client = _validate_authorize_params(
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )
    hidden = _hidden_fields(
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        state=state,
        scope=scope,
    )
    return HTMLResponse(_login_page(client_name=client.client_name, error=None, hidden=hidden))


@router.post("/oauth/authorize", response_model=None)
async def authorize_submit(
    api_key: str = Form(...),
    response_type: str = Form("code"),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str | None = Form(None),
    code_challenge: str | None = Form(None),
    code_challenge_method: str | None = Form(None),
    scope: str | None = Form(None),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse | RedirectResponse:
    client = _validate_authorize_params(
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )
    if not settings.app_api_key or not secrets.compare_digest(api_key.strip(), settings.app_api_key):
        hidden = _hidden_fields(
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            state=state,
            scope=scope,
        )
        return HTMLResponse(
            _login_page(client_name=client.client_name, error="Incorrect API key.", hidden=hidden),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    _prune_expired(_auth_codes)
    code = secrets.token_urlsafe(32)
    _auth_codes[code] = _AuthCode(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge or "",
        expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
    )
    params = {"code": code}
    if state is not None:
        params["state"] = state
    return RedirectResponse(url=f"{redirect_uri}?{urlencode(params)}", status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# Token endpoint — authorization_code + PKCE, refresh_token, and the
# pre-existing client_credentials grant used by the ChatGPT Custom GPT Action.
# ---------------------------------------------------------------------------


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = None


@router.post("/oauth/token")
async def issue_token(
    grant_type: str = Form(...),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
    code_verifier: str | None = Form(None),
    refresh_token: str | None = Form(None),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    if grant_type == "authorization_code":
        if not code or not redirect_uri or not code_verifier:
            raise HTTPException(
                status_code=400, detail="code, redirect_uri and code_verifier are required."
            )
        _prune_expired(_auth_codes)
        auth_code = _auth_codes.pop(code, None)
        if auth_code is None:
            raise HTTPException(status_code=400, detail="Invalid or expired authorization code.")
        if auth_code.redirect_uri != redirect_uri or (client_id and auth_code.client_id != client_id):
            raise HTTPException(
                status_code=400, detail="redirect_uri or client_id does not match the authorization request."
            )
        if not _verify_pkce(code_verifier, auth_code.code_challenge):
            raise HTTPException(status_code=400, detail="code_verifier does not match code_challenge.")

        new_refresh_token = secrets.token_urlsafe(32)
        _refresh_tokens[new_refresh_token] = _Token(expires_at=time.time() + REFRESH_TOKEN_TTL_SECONDS)
        return TokenResponse(
            access_token=_issue_access_token(),
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=new_refresh_token,
        )

    if grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(status_code=400, detail="refresh_token is required.")
        _prune_expired(_refresh_tokens)
        if refresh_token not in _refresh_tokens:
            raise HTTPException(status_code=400, detail="Invalid or expired refresh token.")
        return TokenResponse(
            access_token=_issue_access_token(),
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token,
        )

    if grant_type == "client_credentials":
        if not settings.oauth_client_id or not settings.oauth_client_secret:
            raise HTTPException(status_code=503, detail="OAuth not configured on this server.")
        if client_id != settings.oauth_client_id or client_secret != settings.oauth_client_secret:
            raise HTTPException(status_code=401, detail="Invalid client_id or client_secret.")
        return TokenResponse(access_token=settings.app_api_key, expires_in=3600)

    raise HTTPException(status_code=400, detail=f"Unsupported grant_type: {grant_type}")
