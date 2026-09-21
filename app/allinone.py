"""Everything in one container: `python -m app.allinone`.

For platforms that deploy one image as one HTTP app: starts a Temporal dev server, the worker and
the API. Each replica is self-contained (its own Temporal, so plan and apply always meet the same
worker); records, Terraform state, logs and audit events are all outside the container. If any of
the three processes stops, the rest are stopped and the container exits non-zero so the platform
restarts it; deployments that were running are recovered by `app/recovery.py`.

Set FORGEAPI_TEMPORAL_ADDRESS to use an existing Temporal service; no local server is started
then."""

import os
import signal
import socket
import subprocess
import sys
import time

LOCAL_TEMPORAL = "127.0.0.1:7233"


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
    port = os.environ.get("PORT", "8000")
    external = os.environ.get("FORGEAPI_TEMPORAL_ADDRESS")
    env = {**os.environ, "FORGEAPI_TEMPORAL_ADDRESS": external or LOCAL_TEMPORAL}
    children: dict[str, subprocess.Popen] = {}

    def start(name: str, *command: str) -> None:
        children[name] = subprocess.Popen(command, env=env)  # noqa: S603
        print(f"[allinone] started {name} (pid {children[name].pid})", flush=True)

    def stop_all(*_signal) -> None:
        for child in children.values():
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGINT, stop_all)

    if not external:
        start("temporal", "temporal", "server", "start-dev", "--ip", "127.0.0.1",
              "--headless", "--log-level", "warn")  # fmt: skip
        if not _wait_for_port(LOCAL_TEMPORAL, 60):
            print("[allinone] temporal did not start", flush=True)
            stop_all()
            return 1
    start("worker", sys.executable, "-m", "app.worker")
    start("api", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port)  # noqa: S104

    while True:
        for name, child in children.items():
            code = child.poll()
            if code is not None:
                print(f"[allinone] {name} exited with {code}; stopping the rest", flush=True)
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
