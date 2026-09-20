import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import auth
from app.settings import settings

TENANT = "11111111-1111-4111-8111-111111111111"
AUDIENCE = "api://forgeapi-test"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


@pytest.fixture
def entra(monkeypatch):
    """Entra mode with a locally generated signing key standing in for the tenant's JWKS."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, "auth_mode", "entra")
    monkeypatch.setattr(settings, "entra_tenant_id", TENANT)
    monkeypatch.setattr(settings, "entra_audience", AUDIENCE)
    monkeypatch.setattr(auth, "_signing_key", lambda token: key.public_key())

    def mint(**overrides):
        claims = {"iss": ISSUER, "aud": AUDIENCE, "exp": time.time() + 300, "oid": "user-1"}
        return jwt.encode({**claims, **overrides}, key, algorithm="RS256")

    return mint


def _get(client, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get("/deployments/dep_missing", headers=headers)


def test_no_auth_mode_needs_no_token(client):
    assert _get(client).status_code == 404


def test_valid_token_passes_auth(client, entra):
    assert _get(client, entra()).status_code == 404


def test_missing_token_is_401(client, entra):
    response = _get(client)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "overrides",
    [{"aud": "api://someone-else"}, {"iss": "https://evil.example/v2.0"}, {"exp": 1}],
)
def test_wrong_claims_are_401(client, entra, overrides):
    assert _get(client, entra(**overrides)).status_code == 401


def test_wrong_signature_is_401(client, entra):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "exp": time.time() + 300}, other, algorithm="RS256"
    )
    assert _get(client, token).status_code == 401


def test_healthz_stays_open(client, entra):
    assert client.get("/healthz").status_code == 200


def test_entra_mode_without_config_is_503(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "entra")
    assert _get(client).status_code == 503
