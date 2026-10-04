"""Validation of team (business-unit) documents, before they are stored or applied.

A team doc is one business-unit spec in the tenants.yaml shape (docs/tenancy.md). Findings use
the same level/code/message style as `pattern_check`; codes are stable. Everything here is a pure
function of its inputs except the optional local-pattern inspection, which only reads a local
pattern directory (never a git repository or the network).

CLI: `python -m app.team_check tenants.yaml [--catalog patterns.yaml] [--json]` checks a whole
mapping file; exit status 1 when any error is found."""

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import hcl2
import yaml

from app import catalog as catalog_module

TEAM_NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
# Portal routes #teams/new and #teams/import would shadow teams with these names.
RESERVED_NAMES = frozenset({"new", "import"})


def valid_name(name) -> bool:
    return isinstance(name, str) and bool(TEAM_NAME.fullmatch(name)) and name not in RESERVED_NAMES
UNIT_KEYS = {"groups", "inject", "patterns", "regions", "environments"}
ENV_KEYS = {
    "subscription_id", "groups", "network", "budget_monthly", "targets",
    "protected_resource_types", "allow_destroy",
}  # fmt: skip
FILE_KEYS = {"business_units", "operators", "auditors"}
IDENTIFIERS = {"azure": "subscription_id", "aws": "aws_account_id", "gcp": "project_id"}
COMMON_INJECT_KEYS = ("business_unit", "cost_center")  # `environment` is always injected


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning" | "info"
    code: str
    message: str
    team: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class PatternFacts:
    """What a local pattern directory declares; None facts mean the pattern was not inspected."""

    resource_types: frozenset[str]
    variables: frozenset[str]
    has_estimates: bool


def counts(findings: list[Finding]) -> tuple[int, int]:
    return (
        sum(f.level == "error" for f in findings),
        sum(f.level == "warning" for f in findings),
    )


# --- shape -----------------------------------------------------------------------------------


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and not (
        isinstance(value, float) and not math.isfinite(value)
    )


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(_is_text(v) for v in value)


def _shape(doc: Any, team: str) -> list[Finding]:
    """Unknown keys and wrong types. Semantic checks only run on a well-formed doc."""
    out: list[Finding] = []

    def bad(path: str, message: str, code: str = "schema") -> None:
        out.append(Finding("error", code, f"{path}: {message}", team, path))

    if not isinstance(doc, dict):
        bad("team", "must be a mapping")
        return out
    try:
        json.dumps(doc, allow_nan=False)
    except (TypeError, ValueError):
        bad("team", "must contain only JSON values (no dates, NaN or infinity)")
        return out
    for key in sorted(map(str, set(doc) - UNIT_KEYS)):
        message = (
            "is not allowed; operators come only from FORGEAPI_OPERATOR_GROUPS"
            if (key == "operators")
            else "unknown key"
        )
        bad(key, message, "schema_unknown_key")
    if "groups" in doc and not _strings(doc["groups"]):
        bad("groups", "must be a list of nonempty strings")
    for key in ("inject",):
        value = doc.get(key)
        if key in doc and not (
            isinstance(value, dict)
            and all(isinstance(k, str) and _is_scalar(v) for k, v in value.items())
        ):
            bad(key, "must be a mapping of names to scalar values")
    if "patterns" in doc and not _strings(doc["patterns"]):
        bad("patterns", "must be a list of nonempty strings")
    regions = doc.get("regions")
    if "regions" in doc:
        if not isinstance(regions, dict):
            bad("regions", "must be a mapping with `allowed` and `default`")
        else:
            for key in sorted(map(str, set(regions) - {"allowed", "default"})):
                bad(f"regions.{key}", "unknown key", "schema_unknown_key")
            if "allowed" in regions and not _strings(regions["allowed"]):
                bad("regions.allowed", "must be a list of nonempty strings")
            if regions.get("default") is not None and not _is_text(regions["default"]):
                bad("regions.default", "must be a nonempty string")
    environments = doc.get("environments")
    if "environments" in doc and not isinstance(environments, dict):
        bad("environments", "must be a mapping of environment names to settings")
        environments = None
    for env, spec in (environments or {}).items():
        where = f"environments.{env}"
        if not _is_text(env) or not isinstance(spec, dict):
            bad(where, "needs a nonempty name and a mapping of settings")
            continue
        for key in sorted(map(str, set(spec) - ENV_KEYS)):
            bad(f"{where}.{key}", "unknown key", "schema_unknown_key")
        if spec.get("subscription_id") is not None and not _is_text(spec["subscription_id"]):
            bad(f"{where}.subscription_id", "must be a nonempty string")
        if "groups" in spec and not _strings(spec["groups"]):
            bad(f"{where}.groups", "must be a list of nonempty strings")
        network = spec.get("network")
        if "network" in spec and not (
            isinstance(network, dict)
            and all(isinstance(k, str) and _is_scalar(v) for k, v in network.items())
        ):
            bad(f"{where}.network", "must be a mapping of names to scalar values")
        if "protected_resource_types" in spec and not _strings(spec["protected_resource_types"]):
            bad(f"{where}.protected_resource_types", "must be a list of nonempty strings")
        if "allow_destroy" in spec and not isinstance(spec["allow_destroy"], bool):
            bad(f"{where}.allow_destroy", "must be true or false")
        if "targets" in spec and not isinstance(spec["targets"], dict):
            bad(f"{where}.targets", "must be a mapping of cloud names to targets")
        budget = spec.get("budget_monthly")
        if budget is not None and (
            isinstance(budget, bool) or not isinstance(budget, (int, float))
        ):
            bad(f"{where}.budget_monthly", "must be a number")
    return out


def _targets(env: str, spec: dict, team: str) -> list[Finding]:
    out = []
    for cloud, target in (spec.get("targets") or {}).items():
        path = f"environments.{env}.targets.{cloud}"
        if cloud not in IDENTIFIERS:
            out.append(
                Finding("error", "target_malformed", f"{path}: unknown cloud {cloud!r}", team, path)
            )
            continue
        required = {IDENTIFIERS[cloud], "region"}
        if (
            not isinstance(target, dict)
            or set(target) != required
            or any(not _is_text(v) for v in target.values())
        ):
            out.append(
                Finding(
                    "error",
                    "target_malformed",
                    f"{path}: needs exactly {sorted(required)}, each a nonempty string",
                    team,
                    path,
                )
            )
    return out


# --- pattern inspection (local patterns only) -------------------------------------------------


def local_facts(pattern: catalog_module.Pattern) -> PatternFacts | None:
    """Resource types, variables and whether `estimated_costs` is declared, for a pattern that
    is a local directory. A git pattern needs a checkout (network): returns None, so callers
    skip the checks that need it and say so."""
    if not pattern.local:
        return None
    root = Path(pattern.local)
    types: set[str] = set()
    variables: set[str] = set()
    try:
        for tf_file in sorted(root.glob("*.tf")):
            document = hcl2.loads(tf_file.read_text())
            for block in document.get("resource", []):
                types.update(t.strip('"') for t in block)
            for block in document.get("variable", []):
                variables.update(n.strip('"') for n in block)
        config = None
        if (root / "config.yaml").is_file():
            config = yaml.safe_load((root / "config.yaml").read_text())
    except Exception:  # noqa: BLE001 - unreadable pattern: treat as not inspected
        return None
    estimated = isinstance(config, dict) and "estimated_costs" in config
    return PatternFacts(frozenset(types), frozenset(variables), estimated)


# --- team checks -------------------------------------------------------------------------------


def _ok(doc: Any) -> dict:
    return doc if isinstance(doc, dict) else {}


def _group_sets(doc: dict) -> set[str]:
    groups = set(doc.get("groups") or [])
    for spec in (doc.get("environments") or {}).values():
        groups |= set(_ok(spec).get("groups") or [])
    return groups


def check_team(
    name: str,
    doc: Any,
    *,
    catalog: dict[str, Any] | None,
    current_resources: list[dict] | None = None,
    previous: dict | None = None,
    other_teams: dict[str, dict] | None = None,
    facts: dict[str, PatternFacts | None] | None = None,
    contract_findings: dict[str, list[dict]] | None = None,
) -> list[Finding]:
    """All findings for one team doc.

    `catalog`: pattern name -> `catalog.Pattern` (None: unavailable, catalog checks are skipped
    with an info finding). `previous`: the stored doc, for an update; adds the impact findings
    computed against `current_resources` (the team's resources that still exist). `other_teams`:
    the other teams' docs, for overlap and inject-key comparisons. `facts`: pattern name ->
    `PatternFacts` (default: inspected from local patterns). `contract_findings`: pattern name ->
    its `pattern_check` findings (as dicts), when already known."""
    out: list[Finding] = []
    if not valid_name(name):
        out.append(
            Finding(
                "error",
                "name_invalid",
                f"team name must match {TEAM_NAME.pattern} and not be one of "
                f"{sorted(RESERVED_NAMES)}",
                name,
            )
        )
    team = name if isinstance(name, str) else None
    shape = _shape(doc, team)
    out += shape
    if any(f.level == "error" for f in shape):
        return out
    doc = _ok(doc)
    environments = {env: _ok(spec) for env, spec in (doc.get("environments") or {}).items()}

    if not doc.get("groups") and not any(e.get("groups") for e in environments.values()):
        out.append(
            Finding("error", "no_groups", "no group can reach this team: add `groups`", team)
        )
    for other, other_doc in sorted((other_teams or {}).items()):
        if other != name and (shared := _group_sets(doc) & _group_sets(_ok(other_doc))):
            out.append(
                Finding(
                    "warning",
                    "groups_overlap",
                    f"{len(shared)} group(s) also belong to team {other}; callers in both must "
                    "name a business_unit",
                    team,
                )
            )
    regions = _ok(doc.get("regions"))
    default = regions.get("default")
    if default is not None and default not in (regions.get("allowed") or []):
        out.append(
            Finding(
                "error",
                "region_default_not_allowed",
                f"regions.default {default!r} is not in regions.allowed",
                team,
                "regions.default",
            )
        )
    for env, spec in environments.items():
        out += _targets(env, spec, team)
        budget = spec.get("budget_monthly")
        if budget is not None and (not math.isfinite(budget) or budget < 0):
            path = f"environments.{env}.budget_monthly"
            out.append(
                Finding(
                    "error",
                    "budget_invalid",
                    f"{path}: must be finite and not negative",
                    team,
                    path,
                )
            )

    known = catalog or {}
    allowed = sorted(doc.get("patterns") or [])
    skipped: list[str] = []
    if catalog is None:
        out.append(
            Finding(
                "info",
                "catalog_unavailable",
                "pattern catalog not available; pattern checks skipped",
                team,
            )
        )
    else:
        for pattern in allowed:
            if pattern not in known:
                out.append(
                    Finding(
                        "error",
                        "pattern_unknown",
                        f"pattern {pattern!r} is not in the catalog",
                        team,
                        "patterns",
                    )
                )
                continue
            findings = (contract_findings or {}).get(pattern) or []
            if any(f.get("level") == "error" for f in findings):
                out.append(
                    Finding(
                        "warning",
                        "pattern_contract_failing",
                        f"pattern {pattern!r} fails its contract check",
                        team,
                    )
                )
            for env, spec in sorted(environments.items()):
                cloud = known[pattern].cloud
                if cloud and not spec.get("targets"):
                    out.append(
                        Finding(
                            "error",
                            "env_missing_cloud_target",
                            f"environment {env} has no cloud targets but allows {cloud} pattern "
                            f"{pattern!r}",
                            team,
                            f"environments.{env}.targets",
                        )
                    )
                elif cloud and cloud not in spec["targets"]:
                    out.append(
                        Finding(
                            "info",
                            "cloud_not_offered",
                            f"environment {env} does not target {cloud}; {pattern!r} is "
                            "refused there",
                            team,
                            f"environments.{env}.targets",
                        )
                    )

        inspected: dict[str, PatternFacts] = {}
        for pattern in allowed:
            if pattern not in known:
                continue
            found = (
                facts[pattern]
                if facts is not None and pattern in facts
                else (local_facts(known[pattern]) if facts is None else None)
            )
            if found is None:
                skipped.append(pattern)
            else:
                inspected[pattern] = found
        out += _inspect(team, doc, environments, inspected, other_teams or {}, name)
        if skipped:
            out.append(
                Finding(
                    "info",
                    "pattern_inspection_skipped",
                    "resource types, variables and costs not inspected for git-hosted "
                    f"pattern(s) {', '.join(skipped)}; those checks were skipped",
                    team,
                )
            )

    if previous is not None:
        out += impact_findings(team, _ok(previous), doc, current_resources or [])
    return out


def _inspect(team, doc, environments, inspected, other_teams, name) -> list[Finding]:
    out: list[Finding] = []
    # protected_resource_types that no allowed pattern can create are probably typos.
    if inspected and len(inspected) == len(doc.get("patterns") or []):
        types = set().union(*(f.resource_types for f in inspected.values()))
        for env, spec in sorted(environments.items()):
            for rtype in sorted(set(spec.get("protected_resource_types") or []) - types):
                path = f"environments.{env}.protected_resource_types"
                out.append(
                    Finding(
                        "warning",
                        "protected_type_unused",
                        f"{env}: no allowed pattern declares resource type {rtype!r}",
                        team,
                        path,
                    )
                )
    other_network = set()
    for other, other_doc in other_teams.items():
        if other != name:
            for spec in (_ok(other_doc).get("environments") or {}).values():
                other_network |= set(_ok(_ok(spec).get("network")))
    inject = set(doc.get("inject") or {})
    for pattern, found in sorted(inspected.items()):
        for key in COMMON_INJECT_KEYS:
            if key in found.variables and key not in inject:
                out.append(
                    Finding(
                        "warning",
                        "inject_key_missing",
                        f"pattern {pattern!r} declares {key!r} but `inject` does not supply it",
                        team,
                        "inject",
                    )
                )
        for env, spec in sorted(environments.items()):
            have = inject | set(_ok(spec.get("network")))
            for key in sorted((found.variables & other_network) - have):
                out.append(
                    Finding(
                        "warning",
                        "inject_key_missing",
                        f"pattern {pattern!r} declares {key!r}, which other teams supply as a "
                        f"network value, but {env} does not",
                        team,
                        f"environments.{env}.network",
                    )
                )
            if spec.get("budget_monthly") is not None and not found.has_estimates:
                out.append(
                    Finding(
                        "warning",
                        "pattern_unpriced",
                        f"pattern {pattern!r} declares no estimated_costs but {env} has a "
                        "budget; its intents will be refused",
                        team,
                        f"environments.{env}.budget_monthly",
                    )
                )
    return out


# --- impact of a change on what already exists --------------------------------------------------


def _cost(resource: dict) -> float:
    value = resource.get("estimated_monthly_cost")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _reserved(resources: list[dict], env: str) -> float:
    return sum(_cost(r) for r in resources if r.get("environment") == env)


def impact_findings(team: str | None, old: dict, new: dict, resources: list[dict]) -> list[Finding]:
    out: list[Finding] = []
    old_envs = {e: _ok(s) for e, s in (old.get("environments") or {}).items()}
    new_envs = {e: _ok(s) for e, s in (new.get("environments") or {}).items()}
    for env in sorted(old_envs.keys() - new_envs.keys()):
        count = sum(r.get("environment") == env for r in resources)
        if count:
            out.append(
                Finding(
                    "error",
                    "env_has_resources",
                    f"environment {env} cannot be removed: {count} resource(s) still exist in it",
                    team,
                    f"environments.{env}",
                )
            )
    for env in sorted(old_envs.keys() & new_envs.keys()):
        for cloud in sorted({r.get("cloud") for r in resources if r.get("environment") == env}):
            if cloud is None:
                same = old_envs[env].get("subscription_id") == new_envs[env].get("subscription_id")
            else:
                same = _ok(old_envs[env].get("targets")).get(cloud) == _ok(
                    new_envs[env].get("targets")
                ).get(cloud)
            if not same:
                what = f"{cloud} target" if cloud else "subscription"
                out.append(
                    Finding(
                        "error",
                        "placement_change_with_resources",
                        f"{env}: the {what} cannot change while {cloud or 'legacy'} resources "
                        "exist there; destroy them or migrate them first",
                        team,
                        f"environments.{env}",
                    )
                )
    for pattern in sorted(set(old.get("patterns") or []) - set(new.get("patterns") or [])):
        count = sum(r.get("pattern") == pattern for r in resources)
        if count:
            out.append(
                Finding(
                    "warning",
                    "pattern_in_use",
                    f"pattern {pattern!r} removed while {count} resource(s) use it; they can no "
                    "longer be updated or destroyed by members",
                    team,
                    "patterns",
                )
            )
    for env, spec in sorted(new_envs.items()):
        budget, reserved = spec.get("budget_monthly"), _reserved(resources, env)
        if (
            isinstance(budget, (int, float))
            and not isinstance(budget, bool)
            and math.isfinite(budget)
            and budget < reserved
        ):
            out.append(
                Finding(
                    "warning",
                    "budget_below_reserved",
                    f"{env}: budget_monthly {budget:g} is below the {reserved:g} already reserved",
                    team,
                    f"environments.{env}.budget_monthly",
                )
            )
    removed = len(set(old.get("groups") or []) - set(new.get("groups") or []))
    for env in sorted(old_envs.keys() & new_envs.keys()):
        removed += len(
            set(old_envs[env].get("groups") or []) - set(new_envs[env].get("groups") or [])
        )
    if removed:
        out.append(
            Finding(
                "warning", "access_removed", f"{removed} group grant(s) removed", team, "groups"
            )
        )
    return out


def impact(old: dict | None, new: dict, resources: list[dict]) -> dict:
    """Per environment: how many existing resources the change touches, and reserved versus
    the current and new monthly budget."""
    old_envs = {e: _ok(s) for e, s in _ok(old).get("environments", {}).items()}
    new_envs = {e: _ok(s) for e, s in _ok(new).get("environments", {}).items()}
    return {
        "resources": {
            env: sum(r.get("environment") == env for r in resources)
            for env in sorted(old_envs.keys() | new_envs.keys())
        },
        "budgets": {
            env: {
                "reserved": _reserved(resources, env),
                "current_budget": old_envs.get(env, {}).get("budget_monthly"),
                "new_budget": new_envs.get(env, {}).get("budget_monthly"),
            }
            for env in sorted(old_envs.keys() | new_envs.keys())
        },
    }


# --- whole-file checks ------------------------------------------------------------------------


MAX_DOC_BYTES = 1_000_000


class _NoAliasLoader(yaml.SafeLoader):
    """Safe YAML without anchors or aliases: an alias bomb expands exponentially while parsing."""

    def compose_node(self, parent, index):
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise yaml.YAMLError("YAML anchors and aliases are not allowed")
        return super().compose_node(parent, index)


def load_yaml(text: str) -> Any:
    """Parse team YAML: no aliases or anchors, and the result must stay under 1 MB as JSON."""
    doc = yaml.load(text, Loader=_NoAliasLoader)  # noqa: S506 - SafeLoader subclass
    if len(json.dumps(doc, default=str)) > MAX_DOC_BYTES:
        raise ValueError("document too large")
    return doc


def parse_mapping(yaml_text: str) -> tuple[dict[str, Any], list[Finding]]:
    """(name -> unit doc, file-level findings) for a tenants.yaml text. Top-level `operators` and
    `auditors` are accepted but ignored by the database source: they come from settings."""
    out: list[Finding] = []
    try:
        raw = load_yaml(yaml_text)
    except (yaml.YAMLError, RecursionError, ValueError, TypeError):
        return {}, [Finding("error", "yaml_invalid", "the text is not valid YAML")]
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return {}, [Finding("error", "schema", "the file must be a mapping with business_units")]
    for key in sorted(map(str, set(raw) - FILE_KEYS)):
        out.append(Finding("error", "schema_unknown_key", f"{key}: unknown top-level key"))
    for key in ("operators", "auditors"):
        if key in raw:
            if not _strings(raw[key]):
                out.append(Finding("error", "schema", f"{key}: must be a list of group IDs"))
            else:
                out.append(
                    Finding(
                        "info",
                        "top_level_ignored",
                        f"top-level {key} is ignored by the database source; set "
                        f"FORGEAPI_{key.upper().removesuffix('S')}_GROUPS instead",
                    )
                )
    units = raw.get("business_units", {})
    if units is None:
        units = {}
    if not isinstance(units, dict):
        out.append(Finding("error", "schema", "business_units: must be a mapping"))
        units = {}
    for name in units:
        if not isinstance(name, str):
            out.append(Finding("error", "name_invalid", f"team name {name!r} must be a string"))
    return {k: v for k, v in units.items() if isinstance(k, str)}, out


def check_mapping(
    yaml_text: str,
    *,
    catalog: dict[str, Any] | None = None,
    contract_findings: dict[str, list[dict]] | None = None,
    facts: dict[str, PatternFacts | None] | None = None,
) -> list[Finding]:
    """Every finding for a whole tenants.yaml: file-level, then each team (overlaps compare the
    teams of the file with each other)."""
    units, out = parse_mapping(yaml_text)
    for name, doc in units.items():
        out += check_team(
            name,
            doc,
            catalog=catalog,
            other_teams=units,
            facts=facts,
            contract_findings=contract_findings,
        )
    return out


# --- CLI --------------------------------------------------------------------------------------


def _load_catalog(path: Path) -> dict[str, Any] | None:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        return {n: catalog_module._entry(n, s) for n, s in (raw.get("patterns") or {}).items()}
    except (OSError, yaml.YAMLError, catalog_module.CatalogError, TypeError, AttributeError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.team_check", description="Check a tenants.yaml mapping."
    )
    parser.add_argument("mapping", type=Path, help="the tenants.yaml to check")
    parser.add_argument(
        "--catalog", type=Path, default=Path("patterns.yaml"), help="patterns.yaml (default ./)"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        text = args.mapping.read_text()
    except OSError:
        print(f"cannot read {args.mapping}", file=sys.stderr)
        return 2
    findings = check_mapping(text, catalog=_load_catalog(args.catalog))
    errors, warnings = counts(findings)
    if args.json:
        print(
            json.dumps(
                {
                    "findings": [asdict(f) for f in findings],
                    "errors": errors,
                    "warnings": warnings,
                },
                indent=2,
            )
        )
    else:
        for f in findings:
            scope = f"{f.team}: " if f.team else ""
            print(f"{f.level.upper():7} {f.code}: {scope}{f.message}")
        print(f"{errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
