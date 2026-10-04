"""Unrelated invalid Git tag and diagnostic bytes do not break version discovery."""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app import catalog, ledger
from app.main import app
from tests.conftest import git
from tests.test_operations import INTENT

SECRET = "git-encoding-secret"


def test_invalid_byte_nonversion_tag_keeps_semver_and_default_admission(
    pattern_repo, recorded_dispatcher
):
    tag = os.fsdecode(b"alias-\xff")
    git(pattern_repo, "tag", "-a", tag, "-m", "nonversion alias")
    raw = subprocess.run(
        ["git", "ls-remote", "--tags", f"file://{pattern_repo}"],
        check=True,
        capture_output=True,
    ).stdout
    assert b"refs/tags/alias-\xff" in raw  # Prove the fixture reached real Git output.

    client = TestClient(app)
    peeled = git(pattern_repo, "rev-parse", "v1.1.0^{commit}")
    for prefix in ("", "/v1"):
        described = client.get(f"{prefix}/patterns/demo")
        assert described.status_code == 200, described.text
        assert described.json()["versions"] == ["v1.1.0", "v1.0.0"]
        assert described.json()["commit"] == peeled
    body = {key: value for key, value in INTENT.items() if key != "version"}
    responses = [
        client.post(
            f"{prefix}/operations", json=body, headers={"Idempotency-Key": "encoding-tag"}
        )
        for prefix in ("", "/v1")
    ]
    assert all(response.status_code == 202 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert responses[0].json()["commit"] == peeled
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
        accepted = con.execute("SELECT count(*) FROM events WHERE outcome='accepted'").fetchone()[0]
        assert accepted == 1
    assert len(recorded_dispatcher.pending) == 1


def test_nonzero_git_with_invalid_diagnostic_bytes_stays_sanitized(tmp_path, monkeypatch):
    executable = tmp_path / "git"
    diagnostic = SECRET.encode() + bytes([255])
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        f"os.write(2, {diagnostic!r})\n"
        "sys.exit(1)\n"
    )
    executable.chmod(0o755)
    with monkeypatch.context() as scoped:
        scoped.setenv("PATH", str(tmp_path))
        with pytest.raises(catalog.CatalogError) as failure:
            catalog._git("ls-remote", "--tags", "file:///unused")
    assert SECRET not in str(failure.value)
