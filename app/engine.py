"""The engine container: Temporal server + Temporal web UI + the forgeapi worker.

`python -m app.engine`. For a two-app layout where the API is one HTTP app and this is the other.
Temporal listens on 7233 (gRPC, for the API and the worker here) and serves its UI on the app's
HTTP port (8000, or $PORT), which the platform puts Easy Auth in front of. The worker connects to
the local server. If any process stops, the rest are stopped and the container exits non-zero so
the platform restarts it; a deployment that was running is recovered by `app/recovery.py`.

The Temporal dev server keeps history in memory: it is for getting started. To use an existing
Temporal service instead, set FORGEAPI_TEMPORAL_ADDRESS and only the worker is started."""

import os
import signal
import socket
import subprocess
import sys
import time

LOCAL = "127.0.0.1:7233"


def _wait_for_port(address: str, seconds: int) -> bool:
    host, port = address.rsplit(":", 1)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex((host, int(port))) == 0:
                return True
        time.sleep(0.5)
    return False


def main() -> int:
    ui_port = os.environ.get("PORT", "8000")
    external = os.environ.get("FORGEAPI_TEMPORAL_ADDRESS")
    env = {**os.environ, "FORGEAPI_TEMPORAL_ADDRESS": external or LOCAL}
    children: dict[str, subprocess.Popen] = {}

    def start(name: str, *command: str) -> None:
        children[name] = subprocess.Popen(command, env=env)  # noqa: S603
        print(f"[engine] started {name} (pid {children[name].pid})", flush=True)

    def stop_all(*_signal) -> None:
        for child in children.values():
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGINT, stop_all)

    if not external:
        start(
            "temporal", "temporal", "server", "start-dev",
            "--ip", "0.0.0.0", "--port", "7233",
            "--ui-ip", "0.0.0.0", "--ui-port", ui_port, "--ui-disable-news-fetch",
            "--log-level", "warn",
        )  # fmt: skip
        if not _wait_for_port(LOCAL, 60):
            print("[engine] temporal did not start", flush=True)
            stop_all()
            return 1
    start("worker", sys.executable, "-m", "app.worker")

    while True:
        for name, child in children.items():
            code = child.poll()
            if code is not None:
                print(f"[engine] {name} exited with {code}; stopping the rest", flush=True)
                stop_all()
                for other in children.values():
                    try:
                        other.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        other.kill()
                return code or 1
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
