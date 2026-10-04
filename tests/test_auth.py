import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.requests import Request

from app import auth
from app.settings import settings
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit

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


def _request(client):
    """A bare ASGI request carrying only what require_caller's "none" branch reads."""
    return Request({"type": "http", "method": "GET", "path": "/x", "headers": [], "client": client})


@pytest.mark.parametrize(
    "host,allowed",
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("10.0.0.5", False),
        ("8.8.8.8", False),
        ("testclient", False),  # starlette TestClient's default host; not a parseable address
        (None, False),  # request.client missing entirely
    ],
)
def test_none_mode_gates_on_loopback_unless_the_flag_is_set(monkeypatch, host, allowed):
    monkeypatch.setattr(settings, "allow_unauthenticated_remote", False)
    request = _request((host, 1) if host is not None else None)
    if allowed:
        assert auth.require_caller(request).id == "local"
    else:
        with pytest.raises(auth.HTTPException) as exc:
            auth.require_caller(request)
        assert exc.value.status_code == 401
        assert exc.value.detail == "authentication is not configured for remote callers"


def test_allow_unauthenticated_remote_lets_a_remote_caller_through(monkeypatch):
    monkeypatch.setattr(settings, "allow_unauthenticated_remote", True)
    assert auth.require_caller(_request(("8.8.8.8", 1))).id == "local"


def test_entra_mode_ignores_the_flag(client, entra, monkeypatch):
    monkeypatch.setattr(settings, "allow_unauthenticated_remote", False)
    # The `client` fixture's TestClient host is "testclient" (non-loopback); entra mode never
    # reaches the loopback check at all, so a valid token still passes.
    assert _get(client, entra()).status_code == 404


def test_easyauth_mode_ignores_the_flag(client, monkeypatch):
    monkeypatch.setattr(settings, "allow_unauthenticated_remote", False)
    monkeypatch.setattr(settings, "auth_mode", "easyauth")
    principal = base64.b64encode(json.dumps({"claims": [{"typ": "oid", "val": "u1"}]}).encode())
    headers = {"x-ms-client-principal": principal.decode()}
    response = client.get("/deployments/dep_missing", headers=headers)
    assert response.status_code == 404  # trusted header wins; the flag is irrelevant here


def test_remote_refusal_on_a_mutating_route_is_audited(monkeypatch, agent_client):
    from app import ledger

    monkeypatch.setattr(settings, "allow_unauthenticated_remote", False)
    assert submit(agent_client, "remote-refused").status_code == 401
    with ledger.connect(write=False) as con:
        query = "SELECT actor, action, outcome FROM events ORDER BY seq DESC LIMIT 1"
        row = con.execute(query).fetchone()
    assert (row["actor"], row["action"]) == ("unauthenticated", "POST /operations")
    assert row["outcome"] == "refused"
