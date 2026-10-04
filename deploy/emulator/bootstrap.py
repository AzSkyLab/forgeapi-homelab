"""Register isolated, tagged emulator fixtures; preserve existing repos and runtime state."""

import subprocess
import sys
from pathlib import Path

import yaml

root = Path("/data/emulator-patterns")
root.mkdir(exist_ok=True)
patterns = {}
placed = "--placed" in sys.argv
for provider in ("azure", "aws", "gcp"):
    repo = root / provider
    prefix = "floci-placed" if placed else "floci"
    source = Path(f"/app/examples/{prefix}-{provider}/main.tf").read_bytes()
    if not repo.exists():
        repo.mkdir()
        (repo / "main.tf").write_bytes(source)
        for args in (
            ["init", "-q", "-b", "main"],
            ["add", "main.tf"],
            ["-c", "user.name=ForgeAPI emulator", "-c", "user.email=emulator@localhost",
             "commit", "-qm", "Emulator fixture"],
            ["tag", "v1.0.0"],
        ):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
    elif (repo / "main.tf").read_bytes() != source:
        raise RuntimeError(f"Fixture changed: version {provider} explicitly before upgrading")
    subprocess.run(["git", "-C", str(repo), "rev-parse", "v1.0.0"], check=True)
    patterns[f"floci-{provider}"] = {"repo": f"file://{repo}"}
    if placed:
        patterns[f"floci-{provider}"]["cloud"] = provider
Path("/data/emulator-patterns.yaml").write_text(yaml.safe_dump({"patterns": patterns}))
