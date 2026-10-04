"""The real HTTP opener never forwards a bearer token to another origin."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.client import Client, ClientError

TOKEN = "dummy-transport-token"
MARKER = "redirect-response-marker"
OLD_DISCOVERY = Path(__file__).parent / "fixtures/v1/old_discovery.json"


@contextmanager
def server(seen, *, status=200, body=b"{}", location=None):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(status, MARKER if location else "OK")
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_does_not_forward_bearer_to_other_origin(status):
    first, second = [], []
    with (
        server(second) as destination,
        server(first, status=status, body=MARKER.encode(), location=destination + "/stolen") as api,
    ):
        client = Client(api, token=TOKEN, timeout=2)
        with pytest.raises(ClientError) as error:
            client.discover()
    assert first == [("/agent", f"Bearer {TOKEN}")]
    assert second == []
    assert TOKEN not in str(error.value) and MARKER not in str(error.value)


def test_discovery_cross_origin_link_is_rejected_before_request():
    first, second = [], []
    with server(second) as destination:
        discovery = json.loads(OLD_DISCOVERY.read_text())
        discovery["links"]["submit"] = destination + "/operations"
        with server(first, body=json.dumps(discovery).encode()) as api:
            client = Client(api, token=TOKEN, timeout=2)
            with pytest.raises(ClientError, match="unsafe link") as error:
                client.discover()
    assert first == [("/agent", f"Bearer {TOKEN}")]
    assert second == []
    assert TOKEN not in str(error.value) and MARKER not in str(error.value)
