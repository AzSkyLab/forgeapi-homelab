import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.legacy import app, get_dispatcher
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
def isolated_settings(request, tmp_path, monkeypatch, pattern_repo, tmp_path_factory):
    """Each test gets its own data dir, catalog and default (no-auth, no-Azure) settings."""
    if request.config.getoption("--floci"):
        # Cloud providers are hundreds of MB: one shared cache (Terraform symlinks from it)
        # instead of a copy per test, which filled a 31 GB tmpfs.
        shared = tmp_path_factory.getbasetemp() / "plugin-cache"
        shared.mkdir(exist_ok=True)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "plugin-cache").symlink_to(shared)
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
    # TestClient's "testclient" host is not loopback; tests exercise "none" mode from off-box.
    monkeypatch.setattr(settings, "allow_unauthenticated_remote", True)
    monkeypatch.setattr(settings, "github_token", None)
    for name in ("github_app_id", "github_app_installation_id", "github_app_private_key"):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "db_backend", "sqlite")
    monkeypatch.setattr(settings, "tenants_path", None)
    monkeypatch.setattr(settings, "tenants_yaml", None)
    monkeypatch.setattr(settings, "dev_groups", "")
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


def pytest_addoption(parser):
    parser.addoption("--floci", action="store_true", help="Run real AWS/Azure/GCP Floci tests")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "floci: requires the local AWS, Azure and GCP Floci emulators"
    )


@pytest.fixture
def recorded_dispatcher():
    from app.dispatch import get_dispatcher
    from app.main import app as operation_app
    from tests.operation_support import RecordingDispatcher

    recorder = RecordingDispatcher()
    previous = operation_app.dependency_overrides.copy()
    operation_app.dependency_overrides[get_dispatcher] = lambda: recorder
    try:
        yield recorder
    finally:
        operation_app.dependency_overrides = previous


@pytest.fixture(scope="session", autouse=True)
def terraform_provider_mirror(request, tmp_path_factory):
    """Point Terraform at a persistent filesystem mirror so `init` never hits the registry.

    Providers are mirrored once per example change into a per-user
    cache; every later run, and every per-test data dir, installs from disk.
    """
    terraform = shutil.which("terraform")
    if not terraform:
        yield
        return
    names = ["local-file", "azure-identity-check"]
    if request.config.getoption("--floci"):
        names += ["floci-aws", "floci-azure", "floci-gcp"]  # floci-placed-* pin the same versions
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    mirror = cache / "forgeapi-tests" / "terraform-providers"
    (mirror / ".markers").mkdir(parents=True, exist_ok=True)
    sources: set[str] = set()
    for name in names:
        example = REPO / "examples" / name
        text = "".join(f.read_text() for f in sorted(example.glob("*.tf")))
        sources |= {
            f"registry.terraform.io/{s}" for s in re.findall(r'source\s*=\s*"([^"]+)"', text)
        }
        marker = mirror / ".markers" / name
        stamp = hashlib.sha256(text.encode()).hexdigest()
        if not marker.exists() or marker.read_text() != stamp:
            subprocess.run(
                [terraform, "providers", "mirror", str(mirror)],
                cwd=example,
                check=True,
                capture_output=True,
            )
            marker.write_text(stamp)
    listed = ", ".join(f'"{s}"' for s in sorted(sources))
    config = tmp_path_factory.mktemp("terraform-cli") / "terraform.rc"
    config.write_text(
        "provider_installation {\n"
        f'  filesystem_mirror {{\n    path = "{mirror}"\n    include = [{listed}]\n  }}\n'
        f"  direct {{\n    exclude = [{listed}]\n  }}\n"
        "}\n"
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TF_CLI_CONFIG_FILE", str(config))
        yield
