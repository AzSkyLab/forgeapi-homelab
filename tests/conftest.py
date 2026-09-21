import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app, get_dispatcher
from app.settings import settings

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "local-file"


def git(cwd: Path, *args: str) -> str:
    identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
    done = subprocess.run(["git", *identity, *args], cwd=cwd, check=True, capture_output=True)
    return done.stdout.decode().strip()


@pytest.fixture
def pattern_repo(tmp_path) -> Path:
    """A real git repo standing in for a GitHub pattern repo: v1.0.0, then v1.1.0 adds `suffix`."""
    repo = tmp_path / "terraform-pattern-demo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "main.tf").write_text((EXAMPLE / "main.tf").read_text())
    git(repo, "add", "."), git(repo, "commit", "-qm", "v1.0.0"), git(repo, "tag", "v1.0.0")
    (repo / "main.tf").write_text(
        (EXAMPLE / "main.tf")
        .read_text()
        .replace("content  = var.content", 'content  = "${var.content}${var.suffix}"')
        + '\nvariable "suffix" {\n  type    = string\n  default = ""\n}\n'
    )
    git(repo, "commit", "-qam", "v1.1.0"), git(repo, "tag", "-a", "v1.1.0", "-m", "annotated")
    git(repo, "tag", "not-a-version")
    return repo


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch, pattern_repo):
    """Each test gets its own data dir, catalog and default (no-auth, no-Azure) settings."""
    catalog_file = tmp_path / "patterns.yaml"
    catalog_file.write_text(
        yaml.safe_dump(
            {
                "patterns": {
                    "demo": {"repo": f"file://{pattern_repo}"},
                    "local-file": {"local": str(EXAMPLE)},
                }
            }
        )
    )
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "catalog_path", catalog_file)
    monkeypatch.setattr(settings, "auth_mode", "none")
    monkeypatch.setattr(settings, "github_token", None)
    for name in ("github_app_id", "github_app_installation_id", "github_app_private_key"):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "db_backend", "sqlite")
    monkeypatch.setattr(settings, "azure_use_managed_identity", False)
    monkeypatch.setattr(settings, "azure_federated_client_id", None)
    for name in (
        "azure_tenant_id",
        "azure_subscription_id",
        "azure_client_id",
        "azure_client_certificate_path",
        "state_resource_group",
        "state_storage_account",
    ):
        monkeypatch.setattr(settings, name, None)


@pytest.fixture
def dispatched():
    """Replaces Temporal with a recorder."""
    calls: list[str] = []

    async def fake(deployment_id: str, action: str = "deploy") -> None:
        calls.append(deployment_id if action == "deploy" else f"{action}:{deployment_id}")

    app.dependency_overrides[get_dispatcher] = lambda: fake
    yield calls
    app.dependency_overrides.clear()


@pytest.fixture
def client(dispatched):
    return TestClient(app)
