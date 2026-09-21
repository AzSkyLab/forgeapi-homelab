"""Optional Entra bearer-token validation. AUTH_MODE=none (default) is for loopback development."""

import base64
import json
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.settings import settings
from app.tenants import Caller

_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _jwks_client(tenant_id: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")


def _signing_key(token: str):
    return _jwks_client(settings.entra_tenant_id).get_signing_key_from_jwt(token).key


_OID = ("http://schemas.microsoft.com/identity/claims/objectidentifier", "oid")


def _from_easy_auth(request: Request) -> Caller:
    header = request.headers.get("x-ms-client-principal")
    if not header:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no authenticated principal")
    try:
        claims = json.loads(base64.b64decode(header)).get("claims", [])
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid principal header") from None
    caller_id = next((c["val"] for c in claims if c.get("typ") in _OID), "unknown")
    return Caller(caller_id, frozenset(c["val"] for c in claims if c.get("typ") == "groups"))


def require_caller(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> Caller:
    """Who is calling and which Entra groups they are in."""
    if settings.auth_mode == "none":
        groups = frozenset(g.strip() for g in settings.dev_groups.split(",") if g.strip())
        return Caller("local", groups)
    if settings.auth_mode == "easyauth":
        return _from_easy_auth(request)
    if not (settings.entra_tenant_id and settings.entra_audience):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Entra auth is not configured")
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None:
        raise unauthorized
    try:
        claims = jwt.decode(
            creds.credentials,
            _signing_key(creds.credentials),
            algorithms=["RS256"],
            audience=settings.entra_audience,
            issuer=f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0",
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError:
        # Never echo the token or the raw validation error.
        raise unauthorized from None
    if "groups" not in claims and "_claim_names" in claims:
        # Too many groups to fit in the token. Guessing would either lock people out or let
        # them in wrongly, so say what to fix.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "your token omits group membership (too many groups); ask the platform team to "
            "configure the API's app registration to emit only groups assigned to it",
        )
    caller_id = claims.get("oid") or claims.get("sub") or "unknown"
    return Caller(caller_id, frozenset(claims.get("groups") or []))
