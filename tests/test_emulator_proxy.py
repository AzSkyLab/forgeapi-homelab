"""The emulator HTTP router must not become a general outbound proxy."""

from http.client import HTTPConnection
from threading import Thread

import pytest

from deploy.emulator.storage_proxy import server


@pytest.mark.parametrize("method,target,status", [
    ("GET", "http://example.invalid/", 403),
    ("GET", "http://127.0.0.1/", 403),
    ("GET", "http://storage.blob.core.windows.net:81/", 403),
    ("CONNECT", "storage.blob.core.windows.net:443", 501),
])
def test_non_emulator_destinations_and_tunnels_are_refused(method, target, status):
    proxy = server(port=0)
    thread = Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", proxy.server_port, timeout=2)
    try:
        connection.request(method, target)
        assert connection.getresponse().status == status
    finally:
        connection.close()
        proxy.shutdown()
        proxy.server_close()
        thread.join()
