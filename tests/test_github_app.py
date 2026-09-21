import io
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import catalog, github_app
from app.settings import settings


@pytest.fixture
def app_configured(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setattr(settings, "github_app_id", "12345")
    monkeypatch.setattr(settings, "github_app_installation_id", "678")
    # Key Vault / env often flattens newlines; both forms must work.
    monkeypatch.setattr(settings, "github_app_private_key", pem.replace("\n", "\\n"))
    monkeypatch.setattr(github_app, "_cached", None)
    calls = []

    def fake_urlopen(request, timeout):
        claims = jwt.decode(
            request.headers["Authorization"].removeprefix("Bearer "),
            key.public_key(),
            algorithms=["RS256"],
        )
        calls.append((request.full_url, request.get_method(), claims))
        expires = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return io.BytesIO(
            json.dumps({"token": f"ghs_{len(calls)}", "expires_at": expires}).encode()
        )

    monkeypatch.setattr(github_app.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_mints_and_caches_an_installation_token(app_configured):
    assert github_app.installation_token() == "ghs_1"
    assert github_app.installation_token() == "ghs_1"  # cached, not re-minted

    url, method, claims = app_configured[0]
    assert len(app_configured) == 1
    assert (url, method) == ("https://api.github.com/app/installations/678/access_tokens", "POST")
    assert claims["iss"] == "12345" and claims["exp"] - claims["iat"] <= 600


def test_app_token_is_used_for_git_and_beats_a_static_token(app_configured, monkeypatch):
    monkeypatch.setattr(settings, "github_token", "static-token")
    env = catalog.git_env()
    assert "x-access-token:ghs_1@github.com" in env["GIT_CONFIG_KEY_0"]
    assert "static-token" not in json.dumps(env)


def test_enterprise_server_host(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "t")
    monkeypatch.setattr(settings, "github_host", "github.example.internal")
    env = catalog.git_env()
    assert (
        env["GIT_CONFIG_KEY_0"] == "url.https://x-access-token:t@github.example.internal/.insteadOf"
    )
    assert env["GIT_CONFIG_VALUE_0"] == "https://github.example.internal/"


def test_no_credentials_means_no_git_config():
    assert "GIT_CONFIG_COUNT" not in catalog.git_env()
