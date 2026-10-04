"""Terraform/git child processes must never inherit FORGEAPI_* secrets, and the git token is
minted only for `init` (the one command that fetches pattern modules from git)."""

import json

import pytest

from app import terraform
from app.settings import settings


def _fake_bin(tmp_path):
    """A stand-in `terraform` that dumps its own received environment, proving the real
    subprocess boundary rather than just the dict `_env`/`git_env` build in-process."""
    path = tmp_path / "terraform"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "open(sys.argv[-1], 'w').write(json.dumps(dict(os.environ)))\n"
    )
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    # Secrets arrive as FORGEAPI_* env vars (per settings.py); prove they never reach a child
    # regardless of whether `settings` itself was built from them.
    monkeypatch.setenv("FORGEAPI_GITHUB_APP_PRIVATE_KEY", "pem-secret")
    monkeypatch.setenv("FORGEAPI_GITHUB_TOKEN", "env-secret")
    monkeypatch.setattr(settings, "github_token", "settings-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")


def _child_env(tmp_path, monkeypatch, command):
    monkeypatch.setattr(settings, "terraform_bin", str(_fake_bin(tmp_path)))
    (terraform.deployment_dir("dep-env") / "work").mkdir(parents=True, exist_ok=True)
    out = tmp_path / f"{command}.json"
    terraform._run("dep-env", command, str(out))
    return json.loads(out.read_text())


@pytest.mark.parametrize("command", ["plan", "apply"])
def test_plan_and_apply_env_has_no_secrets_and_no_git_token(tmp_path, monkeypatch, command):
    env = _child_env(tmp_path, monkeypatch, command)
    assert not [k for k in env if k.startswith("FORGEAPI_")]
    assert "GIT_CONFIG_COUNT" not in env


def test_init_env_gets_the_token_but_still_no_forgeapi_names(tmp_path, monkeypatch):
    env = _child_env(tmp_path, monkeypatch, "init")
    assert not [k for k in env if k.startswith("FORGEAPI_")]
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert "settings-secret" in env["GIT_CONFIG_KEY_0"]  # settings.github_token, not the env var


@pytest.mark.parametrize("command", ["plan", "apply", "init"])
def test_non_prefixed_vars_pass_through(tmp_path, monkeypatch, command):
    env = _child_env(tmp_path, monkeypatch, command)
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA_TEST"
    assert "PATH" in env
