"""Loopback token endpoint that lets Terraform use a Container Apps managed identity.

Terraform's managed-identity sign-in only speaks the VM metadata protocol (169.254.169.254),
which Container Apps and App Service do not provide. The Azure SDK for Python does work there.
So the worker serves the VM protocol on 127.0.0.1 and answers with tokens from the SDK;
Terraform is pointed at it with ARM_MSI_ENDPOINT. No secret, no app registration, and the
attached identity's own role assignments apply.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app import azure_identity

_PATH = "/metadata/identity/oauth2/token"
_lock = threading.Lock()
_endpoint: str | None = None


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        url = urlparse(self.path)
        resource = parse_qs(url.query).get("resource", [""])[0]
        # `Metadata: true` cannot be set by a browser or a redirect (the guard IMDS itself uses).
        if url.path != _PATH or self.headers.get("Metadata", "").lower() != "true" or not resource:
            return self._send(400, {"error": "invalid_request"})
        try:
            token = azure_identity.credential().get_token(resource.rstrip("/") + "/.default")
        except Exception:
            return self._send(500, {"error": "token_unavailable"})  # no detail; it may be sensitive
        self._send(
            200,
            {
                "access_token": token.token,
                "refresh_token": "",
                "expires_in": str(max(0, int(token.expires_on - time.time()))),
                "expires_on": str(int(token.expires_on)),
                "not_before": str(int(time.time())),
                "resource": resource,
                "token_type": "Bearer",
            },
        )

    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        """Silent: request lines carry nothing useful and tokens must never be logged."""


def endpoint() -> str:
    """URL for ARM_MSI_ENDPOINT. Starts the server once per process, on a free loopback port."""
    global _endpoint
    with _lock:
        if _endpoint is None:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            threading.Thread(target=server.serve_forever, daemon=True, name="msi-shim").start()
            _endpoint = f"http://127.0.0.1:{server.server_address[1]}{_PATH}"
        return _endpoint
