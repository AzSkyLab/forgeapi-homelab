"""Pattern contract checker: rules, catalog checks, CLI and the API endpoint."""

import json
import os
import shutil
import subprocess
import sys
import textwrap

import pytest
import yaml
from fastapi.testclient import TestClient

from app import catalog, pattern_check, policy
from app.main import app
from app.settings import settings
from tests.conftest import EXAMPLE, REPO, git

GOOD_TF = """
terraform {
  required_version = ">= 1.9"
  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}
variable "filename" {
  description = "File name"
  type        = string
}
resource "local_file" "this" {
  filename = "${path.module}/out/${var.filename}"
  content  = "x"
}
output "path" { value = local_file.this.filename }
"""
GOOD_CONFIG = {"description": "d", "estimated_costs": 0}


def module(tmp_path, files=None, config=None, name="mod"):
    root = tmp_path / name
    root.mkdir()
    for file, text in (files or {"main.tf": GOOD_TF}).items():
        (root / file).parent.mkdir(parents=True, exist_ok=True)
        (root / file).write_text(textwrap.dedent(text))
    if config is not None:
        (root / "config.yaml").write_text(
            config if isinstance(config, str) else yaml.safe_dump(config)
        )
    return root


def run(tmp_path, files=None, config=GOOD_CONFIG, **kwargs):
    return pattern_check.check(module(tmp_path, files, config), **kwargs)


def codes(findings, level=None):
    return {f.code for f in findings if level in (None, f.level)}


def replace(**pairs):
    text = GOOD_TF
    for old, new in pairs.items():
        text = text.replace(old.replace("__", " "), new)
    return {"main.tf": text}


def test_good_module_has_no_errors_and_only_expected_warnings(tmp_path):
    findings = run(tmp_path)
    assert codes(findings, "error") == set()
    assert codes(findings, "warning") == {"local_state"}


PLACED = """
terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "6.14.1" }
  }
}
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.aws_account_id]
}
variable "aws_account_id" { type = string }
variable "region" { type = string }
resource "aws_s3_bucket" "b" { bucket = "x" }
"""


def with_terraform(extra):
    return {"main.tf": GOOD_TF + extra}


CASES = [
    # (id, files, config, kwargs, level, code)
    (
        "hcl_parse",
        {"main.tf": GOOD_TF, "bad.tf": 'variable "x" {'},
        GOOD_CONFIG,
        {},
        "error",
        "hcl_parse",
    ),
    ("no_resources", {"main.tf": 'variable "x" {}'}, GOOD_CONFIG, {}, "error", "no_resources"),
    (
        "no_required_version",
        replace(**{'required_version__=__">=__1.9"': ""}),
        GOOD_CONFIG,
        {},
        "warning",
        "no_required_version",
    ),
    (
        "excludes_platform",
        replace(**{">= 1.9": "< 1.10"}),
        GOOD_CONFIG,
        {},
        "error",
        "required_version_excludes_platform",
    ),
    (
        "excludes_platform_pessimistic",
        replace(**{">= 1.9": "~> 1.9.0"}),
        GOOD_CONFIG,
        {},
        "error",
        "required_version_excludes_platform",
    ),
    (
        "unpinned_provider",
        replace(**{'version = "~> 2.5"': ""}),
        GOOD_CONFIG,
        {},
        "warning",
        "unpinned_provider",
    ),
    (
        "undeclared_provider",
        with_terraform('resource "random_id" "r" { byte_length = 2 }'),
        GOOD_CONFIG,
        {},
        "warning",
        "undeclared_provider",
    ),
    (
        "sensitive_input",
        with_terraform('variable "pw" {\n sensitive = true\n type = string\n}'),
        GOOD_CONFIG,
        {},
        "error",
        "sensitive_input",
    ),
    (
        "unchecked_type",
        with_terraform('variable "o" {\n type = object({ a = string })\n}'),
        GOOD_CONFIG,
        {},
        "warning",
        "unchecked_type",
    ),
    (
        "no_description",
        with_terraform('variable "d" {\n type = string\n}'),
        GOOD_CONFIG,
        {},
        "info",
        "no_description",
    ),
    (
        "validation_runtime_only",
        with_terraform(
            'variable "v" {\n type = string\n validation {\n'
            '  condition = length(var.v) > 0 || var.v == "x"\n  error_message = "m"\n }\n}'
        ),
        GOOD_CONFIG,
        {},
        "info",
        "validation_runtime_only",
    ),
    (
        "sensitive_output",
        with_terraform('output "s" {\n value = "x"\n sensitive = true\n}'),
        GOOD_CONFIG,
        {},
        "info",
        "sensitive_output",
    ),
    (
        "secret_like_output",
        with_terraform('output "admin_password" { value = "x" }'),
        GOOD_CONFIG,
        {},
        "warning",
        "secret_like_output",
    ),
    (
        "missing_target_inputs",
        {"main.tf": PLACED.replace('variable "region" { type = string }', "")},
        GOOD_CONFIG,
        {},
        "error",
        "missing_target_inputs",
    ),
    (
        "target_not_wired",
        {"main.tf": PLACED.replace("allowed_account_ids = [var.aws_account_id]", "")},
        GOOD_CONFIG,
        {},
        "warning",
        "target_not_wired",
    ),
    (
        "hardcoded_placement",
        {
            "main.tf": PLACED.replace(
                "allowed_account_ids = [var.aws_account_id]",
                'allowed_account_ids = ["123456789012"]',
            )
        },
        GOOD_CONFIG,
        {},
        "error",
        "hardcoded_placement",
    ),
    (
        "output_reveals_placement",
        {"main.tf": PLACED + 'output "acct" { value = var.aws_account_id }'},
        GOOD_CONFIG,
        {},
        "warning",
        "output_reveals_placement",
    ),
    (
        "location_and_target",
        {"main.tf": PLACED + 'variable "location" { type = string }'},
        GOOD_CONFIG,
        {},
        "info",
        "location_and_target",
    ),
    ("config_not_mapping", None, "- a\n- b\n", {}, "error", "config_invalid"),
    ("no_estimated_costs", None, {"description": "d"}, {}, "warning", "no_estimated_costs"),
    ("no_config_file", None, None, {}, "warning", "no_estimated_costs"),
    (
        "invalid_estimated_costs",
        None,
        {"estimated_costs": {"dev": "free"}},
        {},
        "error",
        "invalid_estimated_costs",
    ),
    (
        "negative_cost",
        None,
        {"estimated_costs": -1},
        {},
        "error",
        "invalid_estimated_costs",
    ),
    (
        "sizing_var_undeclared",
        None,
        {"estimated_costs": 1, "sizing": {"small": {"dev": {"nope": 1}}}},
        {},
        "error",
        "sizing_var_undeclared",
    ),
    (
        "cost_sizing_mismatch_missing_cost",
        None,
        {
            "estimated_costs": {"small": {"dev": 1}},
            "sizing": {
                "small": {"dev": {"filename": "a"}},
                "large": {"dev": {"filename": "b"}},
            },
        },
        {},
        "warning",
        "cost_sizing_mismatch",
    ),
    (
        "cost_sizing_mismatch_extra_cost",
        None,
        {
            "estimated_costs": {"small": {"dev": 1}, "huge": {"dev": 9}},
            "sizing": {"small": {"dev": {"filename": "a"}}},
        },
        {},
        "warning",
        "cost_sizing_mismatch",
    ),
    (
        "unknown_config_key",
        None,
        {"estimated_costs": 0, "sizes": {}},
        {},
        "warning",
        "unknown_config_key",
    ),
    ("local_state", None, GOOD_CONFIG, {}, "warning", "local_state"),
    (
        "azurerm_backend",
        with_terraform('terraform {\n backend "azurerm" {}\n}'),
        GOOD_CONFIG,
        {},
        "info",
        "platform_backend",
    ),
    (
        "hardcoded_backend",
        with_terraform('terraform {\n backend "azurerm" {\n  storage_account_name = "sa"\n }\n}'),
        GOOD_CONFIG,
        {},
        "error",
        "hardcoded_backend",
    ),
    (
        "self_configured_backend",
        with_terraform('terraform {\n backend "s3" {}\n}'),
        GOOD_CONFIG,
        {},
        "warning",
        "self_configured_backend",
    ),
    (
        "provisioner",
        with_terraform(
            'resource "terraform_data" "p" {\n provisioner "local-exec" {\n'
            '  command = "true"\n }\n}'
        ),
        GOOD_CONFIG,
        {},
        "warning",
        "remote_code",
    ),
    (
        "external_data",
        with_terraform('data "external" "e" {\n program = ["true"]\n}'),
        GOOD_CONFIG,
        {},
        "warning",
        "remote_code",
    ),
    (
        "http_data",
        with_terraform('data "http" "h" {\n url = "https://example.invalid"\n}'),
        GOOD_CONFIG,
        {},
        "warning",
        "remote_code",
    ),
    (
        "placement_in_address",
        {
            "main.tf": PLACED
            + 'resource "aws_s3_bucket" "c" {\n for_each = toset([var.aws_account_id])\n'
            "bucket = each.key\n}"
        },
        GOOD_CONFIG,
        {},
        "warning",
        "placement_in_address",
    ),
    (
        "nested_module_variables",
        {"main.tf": GOOD_TF, "modules/inner/main.tf": 'variable "only_inner" {\n type = string\n}'},
        GOOD_CONFIG,
        {},
        "info",
        "nested_module_variables",
    ),
]


@pytest.mark.parametrize(
    "files,config,kwargs,level,code",
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_each_rule_fires_on_a_bad_module_and_not_on_a_good_one(
    tmp_path, files, config, kwargs, level, code
):
    findings = pattern_check.check(module(tmp_path, files, config), **kwargs)
    assert any(f.code == code and f.level == level for f in findings), findings
    clean = pattern_check.check(module(tmp_path, None, GOOD_CONFIG, name="good"))
    if code != "local_state":  # a module without a backend is the good baseline's one warning
        assert code not in codes(clean)
    else:
        assert "local_state" not in codes(
            pattern_check.check(
                module(tmp_path, with_terraform('terraform {\n backend "s3" {}\n}'), name="s3")
            )
        )


def test_findings_carry_file_and_line(tmp_path):
    findings = run(
        tmp_path,
        with_terraform(
            'resource "terraform_data" "p" {\n provisioner "local-exec" {\n  command = "x"\n }\n}'
        ),
    )
    (hit,) = [f for f in findings if f.code == "remote_code"]
    assert hit.file == "main.tf"
    assert hit.line == len(GOOD_TF.splitlines()) + 2  # the provisioner line


def test_sensitive_input_message_points_to_vault_references(tmp_path):
    findings = run(tmp_path, with_terraform('variable "pw" {\n sensitive = true\n}'))
    (hit,) = [f for f in findings if f.code == "sensitive_input"]
    assert "vault reference" in hit.message


def test_inferred_azure_without_targets_is_only_a_warning_but_explicit_is_an_error(tmp_path):
    files = {
        "main.tf": """
        provider "azurerm" {
          features {}
        }
        resource "azurerm_resource_group" "r" {
          name     = "x"
          location = "eastus"
        }
        """
    }
    inferred = run(tmp_path, files)
    assert {f.level for f in inferred if f.code == "missing_target_inputs"} == {"warning"}
    explicit = pattern_check.check(module(tmp_path, files, GOOD_CONFIG, name="b"), cloud="azure")
    assert {f.level for f in explicit if f.code == "missing_target_inputs"} == {"error"}
    assert "subscription_id" in next(
        f.message for f in explicit if f.code == "missing_target_inputs"
    )


def test_terraform_version_constraint_parsing():
    assert pattern_check.allows(">= 1.9", "1.16.5")
    assert pattern_check.allows("~> 1.9", "1.16.5")
    assert pattern_check.allows(">= 1.9, < 2.0", "1.16.5")
    assert not pattern_check.allows("~> 1.9.0", "1.16.5")
    assert not pattern_check.allows("< 1.16", "1.16.5")
    assert not pattern_check.allows("= 1.9.0", "1.16.5")
    assert pattern_check.allows("!= 1.9.0", "1.16.5")
    assert pattern_check.allows("whatever", "1.16.5") is None


def test_required_version_is_checked_against_the_given_platform_version(tmp_path):
    files = replace(**{">= 1.9": "<= 1.20"})
    assert "required_version_excludes_platform" not in codes(run(tmp_path, files))
    other = pattern_check.check(
        module(tmp_path, files, GOOD_CONFIG, name="o"), terraform_version="1.30.0"
    )
    assert "required_version_excludes_platform" in codes(other)


def test_sized_pattern_with_matching_costs_has_no_mismatch(tmp_path):
    config = {
        "estimated_costs": {"small": {"dev": 1}},
        "sizing": {"small": {"dev": {"filename": "a"}}},
    }
    findings = run(tmp_path, None, config)
    assert "cost_sizing_mismatch" not in codes(findings)
    assert codes(findings, "error") == set()


def test_config_found_at_repo_root_fallback(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.yaml").write_text(yaml.safe_dump(GOOD_CONFIG))
    sub = module(repo, None, None, name="pattern")
    assert "no_estimated_costs" in codes(pattern_check.check(sub))
    assert "no_estimated_costs" not in codes(pattern_check.check(sub, config_fallback=repo))


@pytest.mark.parametrize(
    "name,cloud",
    [("floci-placed-aws", "aws"), ("floci-placed-azure", "azure"), ("floci-placed-gcp", "gcp")],
)
def test_placed_examples_have_no_errors_for_their_cloud(name, cloud):
    findings = pattern_check.check(REPO / "examples" / name, cloud=cloud)
    assert codes(findings, "error") == set(), findings
    assert "missing_target_inputs" not in codes(findings)
    assert "target_not_wired" not in codes(findings)
    # Inference alone reaches the same conclusion.
    assert codes(pattern_check.check(REPO / "examples" / name), "error") == set()


def test_local_file_example_has_no_errors():
    assert codes(pattern_check.check(EXAMPLE), "error") == set()


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_real_terraform_validate_on_local_file_example_and_on_a_broken_module(tmp_path):
    assert codes(pattern_check.check(EXAMPLE, terraform=True), "error") == set()
    broken = module(
        tmp_path,
        {"main.tf": GOOD_TF.replace('content  = "x"', "content  = var.undeclared")},
        GOOD_CONFIG,
    )
    findings = pattern_check.check(broken, terraform=True)
    hits = [f for f in findings if f.code == "terraform_validate" and f.level == "error"]
    assert hits and hits[0].file == "main.tf" and hits[0].line
    assert not (broken / ".terraform").exists()  # validated in a temp copy


# --- catalog ---------------------------------------------------------------------------------


def write_catalog(tmp_path, entries):
    path = tmp_path / "cat.yaml"
    path.write_text(yaml.safe_dump({"patterns": entries}))
    return path


def test_catalog_loading_unknown_key_is_a_clean_catalog_error():
    settings.catalog_path.write_text(
        yaml.safe_dump({"patterns": {"x": {"local": "examples/local-file", "colour": "red"}}})
    )
    with pytest.raises(catalog.CatalogError, match="unknown key.*colour"):
        catalog.load()


def test_catalog_loading_non_mapping_entry_is_a_clean_catalog_error():
    settings.catalog_path.write_text(yaml.safe_dump({"patterns": {"x": "oops"}}))
    with pytest.raises(catalog.CatalogError):
        catalog.load()


def test_catalog_check_accepts_a_good_catalog(tmp_path, pattern_repo):
    path = write_catalog(
        tmp_path,
        {
            "demo": {"repo": f"file://{pattern_repo}", "default_version": "v1.0.0"},
            "local-file": {"local": str(EXAMPLE)},
        },
    )
    findings = pattern_check.check_catalog(path)
    assert codes(findings, "error") == set(), findings
    assert "non_semver_tags" in codes(findings, "info")  # the repo has a `not-a-version` tag


def test_catalog_check_flags_each_problem(tmp_path, pattern_repo):
    empty = tmp_path / "empty-repo"
    empty.mkdir()
    git(empty, "init", "-q", "-b", "main")
    (empty / "main.tf").write_text(GOOD_TF)
    git(empty, "add", "."), git(empty, "commit", "-qm", "c")
    git(empty, "tag", "latest")
    path = write_catalog(
        tmp_path,
        {
            "unknown": {"local": str(EXAMPLE), "colour": "red"},
            "bad-cloud": {"local": str(EXAMPLE), "cloud": "oracle"},
            "both": {"local": str(EXAMPLE), "repo": "github.com/x/y"},
            "neither": {},
            "bad-default": {"repo": f"file://{pattern_repo}", "default_version": "v9.9.9"},
            "no-tags": {"repo": f"file://{empty}"},
            "bad-path": {"repo": f"file://{pattern_repo}", "path": "nope"},
            "unreachable": {"repo": f"file://{tmp_path}/does-not-exist"},
            "missing-local": {"local": str(tmp_path / "missing")},
        },
    )
    found = {(f.file.split(":")[1], f.code) for f in pattern_check.check_catalog(path)}
    assert {
        ("unknown", "unknown_catalog_key"),
        ("bad-cloud", "invalid_cloud"),
        ("both", "local_and_repo"),
        ("neither", "no_source"),
        ("bad-default", "default_version_missing"),
        ("no-tags", "no_versions"),
        ("bad-path", "path_missing"),
        ("unreachable", "repo_unreachable"),
        ("missing-local", "path_missing"),
    } <= found
    assert all(
        f.level == "error" for f in pattern_check.check_catalog(path) if f.code != "non_semver_tags"
    )


def test_repo_catalog_passes_the_checker_locally():
    findings = [
        f
        for f in pattern_check.check_catalog(REPO / "patterns.yaml")
        if f.code not in ("repo_unreachable",)
    ]
    assert not [f for f in findings if f.code in ("unknown_catalog_key", "invalid_cloud")]


# --- CLI -------------------------------------------------------------------------------------


def cli(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FORGEAPI_")}
    env["PYTHONPATH"] = str(REPO)
    return subprocess.run(
        [sys.executable, "-m", "app.pattern_check", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_exit_zero_with_snippet_and_json(tmp_path):
    good = module(tmp_path, None, GOOD_CONFIG)
    done = cli(tmp_path, str(good))
    assert done.returncode == 0, done.stderr
    assert "WARNING" in done.stdout and "[local_state]" in done.stdout
    assert "patterns:" in done.stdout and "local:" in done.stdout
    assert "tenants.yaml" in done.stdout
    parsed = json.loads(cli(tmp_path, str(good), "--json").stdout)
    assert parsed["errors"] == 0 and parsed["warnings"] >= 1
    assert parsed["findings"][0].keys() == {"level", "code", "message", "file", "line"}
    assert "patterns:" in parsed["registration"]


def test_cli_exit_one_on_errors_and_no_snippet(tmp_path):
    bad = module(tmp_path, with_terraform('variable "pw" {\n sensitive = true\n}'), GOOD_CONFIG)
    done = cli(tmp_path, str(bad))
    assert done.returncode == 1
    assert "ERROR" in done.stdout and "main.tf:" in done.stdout
    assert "Registration" not in done.stdout
    parsed = json.loads(cli(tmp_path, str(bad), "--json").stdout)
    assert parsed["errors"] == 1 and parsed["registration"] is None


def test_cli_cloud_flag(tmp_path):
    mod = module(tmp_path, {"main.tf": PLACED.replace('variable "region" { type = string }', "")})
    assert cli(tmp_path, str(mod), "--cloud", "aws").returncode == 1
    assert "[missing_target_inputs]" in cli(tmp_path, str(mod), "--cloud", "aws").stdout


def test_cli_git_url_with_ref_and_path(tmp_path, pattern_repo):
    done = cli(tmp_path, f"file://{pattern_repo}", "--ref", "v1.0.0", "--json")
    assert done.returncode == 0, done.stderr
    parsed = json.loads(done.stdout)
    assert parsed["errors"] == 0
    assert "default_version: v1.0.0" in parsed["registration"]
    missing = cli(tmp_path, f"file://{pattern_repo}", "--ref", "v1.0.0", "--path", "nope")
    assert missing.returncode == 1
    unreachable = cli(tmp_path, f"file://{tmp_path}/gone", "--json")
    assert unreachable.returncode == 1
    assert json.loads(unreachable.stdout)["findings"][0]["code"] == "fetch_failed"


def test_cli_catalog_mode(tmp_path, pattern_repo):
    good = write_catalog(tmp_path, {"demo": {"repo": f"file://{pattern_repo}"}})
    assert cli(tmp_path, "--catalog", str(good)).returncode == 0
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"patterns": {"x": {"local": "a", "colour": 1}}}))
    done = cli(tmp_path, "--catalog", str(bad))
    assert done.returncode == 1 and "unknown_catalog_key" in done.stdout


def test_cli_needs_a_source(tmp_path):
    assert cli(tmp_path).returncode == 2


# --- API -------------------------------------------------------------------------------------


@pytest.fixture
def agent_client(recorded_dispatcher):
    policy._check_cache.clear()
    return TestClient(app)


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_endpoint_reports_findings_for_a_version(agent_client, prefix):
    body = agent_client.get(f"{prefix}/patterns/demo/check", params={"version": "v1.0.0"}).json()
    assert body["name"] == "demo" and body["version"] == "v1.0.0"
    assert len(body["commit"]) == 40
    assert body["errors"] == 0
    assert body["warnings"] == sum(f["level"] == "warning" for f in body["findings"])
    assert {"level", "code", "message", "file", "line"} == set(body["findings"][0])
    assert {f["code"] for f in body["findings"]} >= {"no_estimated_costs", "local_state"}
    newest = agent_client.get(f"{prefix}/patterns/demo/check").json()
    assert newest["version"] == "v1.1.0"
    assert agent_client.get(f"{prefix}/patterns/demo/check?version=v9.9.9").status_code == 422


def test_endpoint_local_pattern_and_unknown_pattern(agent_client):
    local = agent_client.get("/patterns/local-file/check").json()
    assert local["commit"] is None and local["errors"] == 0
    assert agent_client.get("/patterns/nope/check").status_code == 404


def test_endpoint_is_cached_per_commit(agent_client, monkeypatch):
    calls = []
    real = pattern_check.check
    monkeypatch.setattr(pattern_check, "check", lambda *a, **k: calls.append(1) or real(*a, **k))
    first = agent_client.get("/patterns/demo/check?version=v1.0.0").json()
    again = agent_client.get("/v1/patterns/demo/check?version=v1.0.0").json()
    assert first == again and len(calls) == 1
    agent_client.get("/patterns/demo/check?version=v1.1.0")
    assert len(calls) == 2
    agent_client.get("/patterns/local-file/check")
    agent_client.get("/patterns/local-file/check")
    assert len(calls) == 4  # unversioned patterns are never cached


def test_endpoint_never_runs_terraform(agent_client, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("terraform must not run")

    monkeypatch.setattr(pattern_check, "_terraform_validate", boom)
    assert agent_client.get("/patterns/demo/check").status_code == 200


def test_endpoint_hides_patterns_the_caller_may_not_use(agent_client, tmp_path, monkeypatch):
    tenants_file = tmp_path / "tenants.yaml"
    tenants_file.write_text(
        yaml.safe_dump(
            {
                "business_units": {
                    "finance": {
                        "groups": ["fin-devs"],
                        "inject": {"business_unit": "finance"},
                        "patterns": ["local-file"],
                        "environments": {"dev": {"subscription_id": "sub-1"}},
                    }
                }
            }
        )
    )
    monkeypatch.setattr(settings, "tenants_path", tenants_file)
    monkeypatch.setattr(settings, "dev_groups", "fin-devs")
    assert agent_client.get("/patterns/local-file/check").status_code == 200
    assert agent_client.get("/patterns/demo/check").status_code == 404
    monkeypatch.setattr(settings, "dev_groups", "someone-else")
    assert agent_client.get("/patterns/local-file/check").status_code == 404


def test_endpoint_with_pattern_in_subdirectory_finds_repo_root_config(agent_client, tmp_path):
    repo = tmp_path / "subrepo"
    (repo / "pattern").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "pattern" / "main.tf").write_text(textwrap.dedent(GOOD_TF))
    (repo / "config.yaml").write_text(yaml.safe_dump(GOOD_CONFIG))
    git(repo, "add", "."), git(repo, "commit", "-qm", "c"), git(repo, "tag", "v1.0.0")
    spec = yaml.safe_load(settings.catalog_path.read_text())
    spec["patterns"]["sub"] = {"repo": f"file://{repo}", "path": "pattern"}
    settings.catalog_path.write_text(yaml.safe_dump(spec))
    body = agent_client.get("/patterns/sub/check").json()
    assert "no_estimated_costs" not in {f["code"] for f in body["findings"]}


def test_discovery_advertises_the_capability(agent_client):
    assert agent_client.get("/v1/agent").json()["capabilities"]["pattern_checks"] is True


def test_symlinked_tf_and_config_are_reported_and_not_read(tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text('variable "leak" { type = string }\n')
    root = module(tmp_path, None, None, name="sym")
    (root / "extra.tf").symlink_to(secret)
    (root / "config.yaml").symlink_to(tmp_path / "outside.txt")
    findings = pattern_check.check(root)
    symlinked = {f.file for f in findings if f.code == "symlinked_file"}
    assert symlinked == {"extra.tf", "config.yaml"}
    assert all(f.level == "error" for f in findings if f.code == "symlinked_file")
    assert "leak" not in str([f.message for f in findings])


def test_catalog_reads_skip_symlinks_outside_the_checkout(tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text("description: stolen\n")
    root = module(tmp_path, None, None, name="loc")
    (root / "config.yaml").symlink_to(outside)
    resolved = catalog.Resolved(catalog.Pattern(name="x", local=str(root)), None, None)
    with pytest.raises(catalog.CatalogError):
        catalog.config(resolved)
    (root / "bad.tf").symlink_to(tmp_path / "outside.yaml")
    assert [v["name"] for v in catalog.variables(resolved)] == ["filename"]
