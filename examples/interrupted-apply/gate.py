"""Test-only apply gate: record a real side effect, then wait for release."""

import os
import time
from pathlib import Path

root = Path(os.environ["GATE_ROOT"])
(root / "effect").write_text("applied\n")
(root / "started").write_text("started\n")
deadline = time.monotonic() + 120
while not (root / "release").exists():
    if time.monotonic() > deadline:
        raise TimeoutError("interrupted apply gate was never released")
    time.sleep(0.05)
(root / "finished").write_text("released\n")
