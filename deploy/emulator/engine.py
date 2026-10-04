"""Trust only this pod's ephemeral Azure emulator CA before starting the normal engine."""

import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

deadline = time.monotonic() + 120
while True:
    try:
        with urllib.request.urlopen("http://localhost:4577/_floci/tls-cert", timeout=5) as reply:
            pem = reply.read().decode()
        if "BEGIN CERTIFICATE" not in pem:
            raise ValueError("Emulator CA not ready")
        break
    except (OSError, ValueError, urllib.error.URLError):
        if time.monotonic() >= deadline:
            raise RuntimeError("Azure emulator CA unavailable") from None
        time.sleep(1)
ca = Path("/data/emulator-ca.pem")
ca.write_text(Path(ssl.get_default_verify_paths().cafile).read_text() + "\n" + pem)
os.environ["SSL_CERT_FILE"] = str(ca)
if "--storage-proxy" in sys.argv:
    subprocess.Popen([sys.executable, "/scripts/storage_proxy.py"])
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:4590"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.execvp("python", ["python", "-m", "app.engine"])
