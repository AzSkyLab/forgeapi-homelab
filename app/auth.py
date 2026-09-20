"""Optional Entra bearer-token validation. AUTH_MODE=none (default) is for loopback development."""

from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.settings import settings

_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _jwks_client(tenant_id: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")


def _signing_key(token: str):
    return _jwks_client(settings.entra_tenant_id).get_signing_key_from_jwt(token).key


def require_caller(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """Return the caller's object ID, or "local" when auth is disabled."""
    if settings.auth_mode == "none":
        return "local"
    if not (settings.entra_tenant_id and settings.entra_audience):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Entra auth is not configured")
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "invalid or missing token",
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
    return claims.get("oid") or claims.get("sub") or "unknown"
