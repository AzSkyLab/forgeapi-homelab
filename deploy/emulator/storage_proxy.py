"""Emulator-only HTTP routing for Azure Storage's provider-generated DNS names.

Floci returns *.core.windows.net URLs on port 80. Forward only those HTTP requests to
the loopback emulator, preserving Host for account/service routing. No public destination,
CONNECT tunnel, credential logging or general-purpose proxying is supported.
"""

import http.client
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class StorageProxy(BaseHTTPRequestHandler):
    def route(self):
        url = urlsplit(self.path)
        if (
            url.scheme != "http"
            or url.port not in (None, 80)
            or not re.fullmatch(
                r"[a-z0-9]{3,24}\.(blob|queue|table|dfs)\.core\.windows\.net", url.hostname or ""
            )
        ):
            self.send_error(403, "Only emulated Azure Storage destinations are allowed")
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in {"host", "proxy-connection", "connection"}
        }
        headers["Host"] = url.netloc
        connection = http.client.HTTPConnection("127.0.0.1", 4577, timeout=30)
        try:
            path = url.path + ("?" + url.query if url.query else "")
            connection.request(self.command, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            connection.close()

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = route

    def log_message(self, *_args):
        pass


def server(port=4590):
    return ThreadingHTTPServer(("127.0.0.1", port), StorageProxy)


if __name__ == "__main__":
    server().serve_forever()
