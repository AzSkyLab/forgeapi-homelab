"""Team document validation: stable finding codes, impact checks and the CLI."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import yaml

from app import team_check
from app.catalog import Pattern
from app.team_check import PatternFacts, check_mapping, check_team

CATALOG = {
    "storage": Pattern(name="storage", repo="github.com/x/storage", cloud="aws"),
    "plain": Pattern(name="plain", repo="github.com/x/plain"),
}
REPO = Path(__file__).resolve().parent.parent
AWS = {"aws": {"aws_account_id": "111", "region": "us-east-1"}}


class _Plain(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def dump(data):
    """YAML without anchors: team YAML parsing refuses aliases."""
    return yaml.dump(data, Dumper=_Plain)


def good(**over):
    team = {
        "groups": ["g1"],
        "patterns": ["storage"],
        "environments": {"dev": {"targets": AWS}},
    }
    return {**team, **over}


def codes(findings, level=None):
    return {f.code for f in findings if level is None or f.level == level}


def check(doc, **kwargs):
    return check_team("finance", doc, catalog=CATALOG, **kwargs)


def test_a_good_team_has_no_errors_or_warnings():
    findings = check(good())
    assert not codes(findings, "error") | codes(findings, "warning")
    assert "pattern_inspection_skipped" in codes(findings, "info")  # git patterns: said so


def test_shape_errors_are_stable_codes_and_stop_semantic_checks():
    assert "schema_unknown_key" in codes(check(good(colour="red")))
    assert "schema_unknown_key" in codes(check(good(operators=["x"])))  # never from a team doc
    for doc in (
        good(groups="g1"),
        good(inject={"a": [1]}),
        good(regions=["eastus"]),
        good(environments={"dev": {"allow_destroy": "yes"}}),
        good(environments={"dev": {"budget_monthly": True}}),
        good(environments={"dev": {"budget_monthly": "5"}}),
        good(environments={"dev": {"nope": 1}}),
        "not a mapping",
    ):
        assert codes(check(doc), "error") & {"schema", "schema_unknown_key"}, doc
    # A malformed document stops at the shape errors: no semantic findings pile on.
    assert codes(check("x")) == {"schema"}


def test_name_pattern():
    assert "name_invalid" in codes(check_team("Bad_Name", good(), catalog=CATALOG))


def test_no_groups_is_an_error_unless_environments_carry_groups():
    assert "no_groups" in codes(check(good(groups=[])), "error")
    only_env = good(groups=[], environments={"dev": {"groups": ["g"], "targets": AWS}})
    assert "no_groups" not in codes(check(only_env))


def test_groups_overlap_is_a_warning():
    findings = check(good(), other_teams={"hr": {"groups": ["g1"]}})
    assert "groups_overlap" in codes(findings, "warning")
    assert "groups_overlap" not in codes(check(good(), other_teams={"hr": {"groups": ["z"]}}))


def test_environment_without_targets_while_allowing_a_cloud_pattern():
    findings = check(good(environments={"dev": {}}))
    (finding,) = [f for f in findings if f.code == "env_missing_cloud_target"]
    assert finding.level == "error" and "storage" in finding.message and "aws" in finding.message
    # A cloud-less pattern needs none; an environment offering another cloud is only informational.
    assert "env_missing_cloud_target" not in codes(check(good(patterns=["plain"])))
    other = {"azure": {"subscription_id": "s", "region": "r"}}
    findings = check(good(environments={"dev": {"targets": other}}))
    assert "env_missing_cloud_target" not in codes(findings)
    assert "cloud_not_offered" in codes(findings, "info")


def test_malformed_targets():
    for target in (
        {"aws": {"aws_account_id": "1"}},
        {"aws": {"aws_account_id": "", "region": "r"}},
        {"aws": {"subscription_id": "1", "region": "r"}},
        {"mars": {"x": "y"}},
        {"aws": "text"},
    ):
        findings = check(good(environments={"dev": {"targets": target}}))
        assert "target_malformed" in codes(findings, "error"), target


def test_region_default_must_be_allowed():
    ok = good(regions={"allowed": ["a", "b"], "default": "a"})
    assert "region_default_not_allowed" not in codes(check(ok))
    bad = good(regions={"allowed": ["a"], "default": "z"})
    assert "region_default_not_allowed" in codes(check(bad), "error")


def test_budget_must_be_finite_and_not_negative():
    for value in (-1, math.inf, math.nan):
        doc = good(environments={"dev": {"targets": AWS, "budget_monthly": value}})
        assert codes(check(doc), "error") & {"budget_invalid", "schema"}, value
    doc = good(environments={"dev": {"targets": AWS, "budget_monthly": 0}})
    assert "budget_invalid" not in codes(check(doc))


def test_pattern_must_be_in_catalog_and_pass_its_contract():
    assert "pattern_unknown" in codes(check(good(patterns=["storage", "ghost"])), "error")
    failing = {"storage": [{"level": "error", "code": "x", "message": "m"}]}
    findings = check(good(), contract_findings=failing)
    assert "pattern_contract_failing" in codes(findings, "warning")
    assert check(good(), contract_findings={"storage": [{"level": "warning"}]})  # not failing


def test_catalog_unavailable_skips_catalog_checks():
    findings = check_team("finance", good(patterns=["ghost"]), catalog=None)
    assert "pattern_unknown" not in codes(findings) and "catalog_unavailable" in codes(findings)


def facts(types=(), variables=(), estimates=False):
    return PatternFacts(frozenset(types), frozenset(variables), estimates)


def test_inject_keys_the_pattern_needs_but_the_team_lacks():
    known = {"storage": facts(variables={"business_unit", "cost_center", "subnet_id"})}
    others = {"hr": {"environments": {"dev": {"network": {"subnet_id": "/x"}}}}}
    findings = check(good(inject={"business_unit": "finance"}), facts=known, other_teams=others)
    messages = [f.message for f in findings if f.code == "inject_key_missing"]
    assert any("cost_center" in m for m in messages)
    assert any("subnet_id" in m for m in messages)
    assert not any("'business_unit'" in m for m in messages)
    fixed = good(
        inject={"business_unit": "finance", "cost_center": "c"},
        environments={"dev": {"targets": AWS, "network": {"subnet_id": "/y"}}},
    )
    assert "inject_key_missing" not in codes(check(fixed, facts=known, other_teams=others))


def test_protected_types_no_pattern_creates_and_unpriced_patterns():
    known = {"storage": facts(types={"aws_s3_bucket"})}
    env = {"targets": AWS, "protected_resource_types": ["aws_s3_bucket", "aws_typo"]}
    findings = check(good(environments={"dev": env}), facts=known)
    unused = [f for f in findings if f.code == "protected_type_unused"]
    assert len(unused) == 1 and "aws_typo" in unused[0].message and unused[0].level == "warning"
    # Not every pattern inspected (a git pattern): the check is skipped and that is said.
    findings = check(good(environments={"dev": env}), facts={"storage": None})
    assert "protected_type_unused" not in codes(findings)
    assert "pattern_inspection_skipped" in codes(findings, "info")
    budgeted = good(environments={"dev": {"targets": AWS, "budget_monthly": 10}})
    assert "pattern_unpriced" in codes(check(budgeted, facts=known), "warning")
    priced = {"storage": facts(types={"aws_s3_bucket"}, estimates=True)}
    assert "pattern_unpriced" not in codes(check(budgeted, facts=priced))


def test_local_patterns_are_inspected(tmp_path):
    (tmp_path / "main.tf").write_text(
        'variable "cost_center" { type = string }\nresource "local_file" "a" {}\n'
    )
    (tmp_path / "config.yaml").write_text("estimated_costs: 0\n")
    local = {"loc": Pattern(name="loc", local=str(tmp_path))}
    found = team_check.local_facts(local["loc"])
    assert found == PatternFacts(frozenset({"local_file"}), frozenset({"cost_center"}), True)
    doc = {"groups": ["g"], "patterns": ["loc"], "environments": {"dev": {}}}
    findings = check_team("t1", doc, catalog=local)
    assert "inject_key_missing" in codes(findings, "warning")
    assert "pattern_inspection_skipped" not in codes(findings)


RESOURCES = [
    {"environment": "dev", "cloud": "aws", "pattern": "storage", "estimated_monthly_cost": 60},
    {"environment": "dev", "cloud": "aws", "pattern": "storage", "estimated_monthly_cost": 40},
]


def test_impact_errors_and_warnings_against_the_stored_team():
    stored = good(
        groups=["g1", "g2"],
        environments={"dev": {"targets": AWS, "budget_monthly": 500}, "prd": {"targets": AWS}},
    )
    new = good(
        groups=["g1"],
        patterns=["plain"],
        environments={
            "dev": {
                "budget_monthly": 50,
                "targets": {"aws": {"aws_account_id": "222", "region": "us-east-1"}},
            }
        },
    )
    resources = [*RESOURCES, {"environment": "prd", "cloud": "aws", "pattern": "storage"}]
    findings = check(new, previous=stored, current_resources=resources)
    assert {"env_has_resources", "placement_change_with_resources"} <= codes(findings, "error")
    assert {"pattern_in_use", "budget_below_reserved", "access_removed"} <= codes(
        findings, "warning"
    )
    (budget,) = [f for f in findings if f.code == "budget_below_reserved"]
    assert "50" in budget.message and "100" in budget.message
    assert "222" not in "".join(f.message for f in findings)  # no identifiers in messages
    summary = team_check.impact(stored, new, resources)
    assert summary["resources"] == {"dev": 2, "prd": 1}
    assert summary["budgets"]["dev"] == {"reserved": 100.0, "current_budget": 500, "new_budget": 50}


def test_changes_without_resources_have_no_impact_findings():
    stored = good(groups=["g1", "g2"], environments={"dev": {"targets": AWS}, "prd": {}})
    new = good(environments={"dev": {"targets": {"aws": {**AWS["aws"], "region": "eu-west-1"}}}})
    findings = check(new, previous=stored, current_resources=[])
    assert not codes(findings, "error") - {"env_missing_cloud_target"}
    assert "access_removed" in codes(findings, "warning")


def test_check_mapping_covers_the_whole_file():
    text = dump(
        {
            "operators": ["ops"],
            "business_units": {
                "finance": good(),
                "hr": good(groups=["g1"], colour="red"),
                "Bad": good(),
            },
            "surprise": 1,
        }
    )
    findings = check_mapping(text, catalog=CATALOG)
    assert {"schema_unknown_key", "name_invalid", "groups_overlap", "top_level_ignored"} <= codes(
        findings
    )
    assert "yaml_invalid" in codes(check_mapping("a: [", catalog=CATALOG))
    assert not codes(check_mapping("business_units: {}\n", catalog=CATALOG), "error")


def run_cli(tmp_path, mapping, *args):
    path = tmp_path / "tenants.yaml"
    path.write_text(mapping)
    env = {k: v for k, v in os.environ.items() if not k.startswith("FORGEAPI_")}
    return subprocess.run(
        [sys.executable, "-m", "app.team_check", str(path), *args],
        cwd=tmp_path,
        env={**env, "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_exit_codes_and_json(tmp_path):
    (tmp_path / "patterns.yaml").write_text(
        yaml.safe_dump({"patterns": {"storage": {"repo": "github.com/x/s", "cloud": "aws"}}})
    )
    ok = dump({"business_units": {"finance": good()}})
    done = run_cli(tmp_path, ok, "--json")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["errors"] == 0
    bad = dump({"business_units": {"finance": good(groups=[]), "hr": good(colour=1)}})
    done = run_cli(tmp_path, bad, "--json")
    assert done.returncode == 1
    report = json.loads(done.stdout)
    assert report["errors"] >= 2 and {f["code"] for f in report["findings"]} >= {"no_groups"}
    text = run_cli(tmp_path, bad)
    assert text.returncode == 1 and "ERROR" in text.stdout and "no_groups" in text.stdout
    missing = subprocess.run(
        [sys.executable, "-m", "app.team_check", "nope.yaml"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 2


def test_portal_route_names_are_reserved():
    from app import team_check

    assert not team_check.valid_name("new") and not team_check.valid_name("import")
    assert team_check.valid_name("newsroom")
    codes = {
        f.code
        for f in team_check.check_team(
            "import", {"groups": ["g"]}, catalog={}, current_resources=[]
        )
    }
    assert "name_invalid" in codes
