"""Short-lived GitHub App installation tokens for reading pattern repos.

Preferred over a personal token: the App belongs to the organisation, the token lasts an hour
and is scoped to the App's repositories and permissions. The private key is expected to arrive
through a Key Vault secret reference, never in a file or image."""

import json
import threading
import time
import urllib.request
from datetime import datetime

import jwt

from app.settings import settings

_lock = threading.Lock()
_cached: tuple[str, float] | None = None  # token, expiry (epoch seconds)


def configured() -> bool:
    return bool(
        settings.github_app_id
        and settings.github_app_installation_id
        and settings.github_app_private_key
    )


def installation_token() -> str:
    global _cached
    with _lock:
        if _cached and _cached[1] - time.time() > 300:
            return _cached[0]
        now = int(time.time())
        assertion = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": str(settings.github_app_id)},
            settings.github_app_private_key.replace("\\n", "\n"),
            algorithm="RS256",
        )
        request = urllib.request.Request(
            f"{settings.github_api_url.rstrip('/')}/app/installations/"
            f"{settings.github_app_installation_id}/access_tokens",
            method="POST",
            headers={
                "Authorization": f"Bearer {assertion}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (https API)
            body = json.load(response)
        expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).timestamp()
        _cached = (body["token"], expires)
        return body["token"]
