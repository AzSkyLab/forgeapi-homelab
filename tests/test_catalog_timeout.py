"""Git subprocess deadlines fail safely and leave later pattern checkouts usable."""

import os
import shutil
import stat
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import catalog, ledger
from app.main import app
from app.settings import settings
from tests.test_operations import INTENT


def fake_git(tmp_path, monkeypatch, command, *, child_marker=None, ready_marker=None):
    real_git = shutil.which("git")
    script = tmp_path / "git"
    if child_marker is None:
        blocked = "time.sleep(0.4)"
    else:
        child = (
            "import time; from pathlib import Path; "
            f"time.sleep(0.7); Path({str(child_marker)!r}).write_text('survived')"
        )
        blocked = (
            f"subprocess.Popen([{sys.executable!r}, '-c', {child!r}], "
            "stdout=sys.stdout, stderr=sys.stderr); "
            f"Path({str(ready_marker)!r}).write_text('spawned'); os._exit(0)"
        )
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys, time\nfrom pathlib import Path\n"
        f"if len(sys.argv) > 1 and sys.argv[1] == {command!r}:\n    {blocked}\n"
        f"else:\n    os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(catalog, "GIT_TIMEOUT", 0.2, raising=False)
    monkeypatch.setattr(catalog, "GIT_DRAIN_TIMEOUT", 0.2, raising=False)


@pytest.mark.parametrize("command", ["ls-remote", "fetch"])
def test_git_timeout_refuses_mutation_and_retry_checks_out_cleanly(
    tmp_path, monkeypatch, pattern_repo, recorded_dispatcher, command
):
    client = TestClient(app)
    body = {**INTENT, "inputs": {"filename": "x.txt", "content": "catalog-secret"}}
    with monkeypatch.context() as scoped:
        fake_git(tmp_path, scoped, command)
        validation = client.post("/v1/intents/validate", json=body)
        refused = client.post(
            "/v1/operations", json=body, headers={"Idempotency-Key": "git-timeout"}
        )
    assert validation.status_code == refused.status_code == 502
    assert validation.json()["error"]["code"] == "catalog_unavailable"
    assert refused.json()["error"]["code"] == "catalog_unavailable"
    assert "catalog-secret" not in validation.text + refused.text
    assert str(pattern_repo) not in validation.text + refused.text
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
        assert [row[0] for row in con.execute("SELECT outcome FROM events")] == ["refused"]
    assert not recorded_dispatcher.pending
    assert not list((settings.data_dir / "pattern-cache").glob("**/.forgeapi-ready"))

    accepted = client.post(
        "/v1/operations", json=body, headers={"Idempotency-Key": "git-timeout"}
    )
    assert accepted.status_code == 202, accepted.text
    assert len(recorded_dispatcher.pending) == 1
    assert list((settings.data_dir / "pattern-cache").glob("**/.forgeapi-ready"))


def test_git_timeout_kills_child_holding_stdout(tmp_path, monkeypatch, pattern_repo):
    marker = tmp_path / "child-survived"
    ready = tmp_path / "child-spawned"
    fake_git(tmp_path, monkeypatch, "ls-remote", child_marker=marker, ready_marker=ready)
    started = time.monotonic()
    with pytest.raises(catalog.CatalogError) as failure:
        catalog._git("ls-remote", "--tags", f"file://{pattern_repo}")
    assert ready.read_text() == "spawned"
    assert str(pattern_repo) not in str(failure.value)
    assert time.monotonic() - started < 0.55
    time.sleep(0.8)  # bounded child lifetime; a surviving child writes this marker
    assert not marker.exists()
