"""Operator-only administration of teams (business units) stored in the database.

Every write runs in ONE ledger transaction: re-read the team, check its revision, validate with
the current resources, record the `accepted` audit event, then write the team and its revision.
So there is no write without its audit event, no check-then-act race with other admin writes,
and a refusal changes nothing. Operators are never stored: they come from settings only."""

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import catalog, ledger, policy, team_check, team_store, tenants
from app.contracts import OperationError
from app.team_check import Finding

NAME_PATTERN = team_check.TEAM_NAME.pattern
MAX_REASON = 500
_REASON = Field(min_length=1, max_length=MAX_REASON, description="Why this change is made.")
_ACK = Field(
    default_factory=list,
    description="Warning codes you accept. Every warning code in the findings must be listed, "
    "otherwise the write is refused with 409 `warnings_not_acknowledged`.",
)
_EXAMPLE_TEAM = {
    "groups": ["00000000-0000-0000-0000-000000000001"],
    "inject": {"business_unit": "finance", "cost_center": "CC-1042"},
    "patterns": ["local-file"],
    "environments": {"dev": {"budget_monthly": 100}},
}


# --- request bodies ----------------------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamValidate(_Body):
    """Check a team document and its impact without writing anything."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"name": "finance", "team": _EXAMPLE_TEAM}]},
    )
    name: str = Field(pattern=NAME_PATTERN, description="Team name; lowercase letters, digits, -.")
    team: dict[str, Any] = Field(
        description="One business-unit spec in the tenants.yaml shape (docs/tenancy.md)."
    )
    reason: str | None = Field(default=None, max_length=MAX_REASON, description="Ignored.")


class TeamCreate(_Body):
    """Create a team; refused on any error finding."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"name": "finance", "team": _EXAMPLE_TEAM, "reason": "onboard finance"}]
        },
    )
    name: str = Field(pattern=NAME_PATTERN, description="Team name; lowercase letters, digits, -.")
    team: dict[str, Any] = Field(description="One business-unit spec in the tenants.yaml shape.")
    reason: str = _REASON


class TeamReplace(_Body):
    """Replace a team's whole document. Needs `If-Match`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "team": _EXAMPLE_TEAM,
                    "reason": "raise the dev budget",
                    "acknowledge_warnings": [],
                }
            ]
        },
    )
    team: dict[str, Any] = Field(description="The new document; replaces the stored one.")
    reason: str = _REASON
    acknowledge_warnings: list[str] = _ACK


class TeamRevert(_Body):
    """Make a past revision current again, as a new revision. Needs `If-Match`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"revision": 1, "reason": "undo budget change", "acknowledge_warnings": []}
            ]
        },
    )
    revision: int = Field(ge=1, description="The revision whose document becomes current.")
    reason: str = _REASON
    acknowledge_warnings: list[str] = _ACK


class TeamArchive(_Body):
    """Archive a team: no new intents or placement; its resources stay visible and destroyable."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"reason": "team disbanded", "force_archive": False}]},
    )
    reason: str = _REASON
    force_archive: bool = Field(
        default=False,
        description="Archive even though the team still has resources that are not destroyed.",
    )


class TeamUnarchive(_Body):
    """Make an archived team active again."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"reason": "team is back"}]}
    )
    reason: str = _REASON


class TeamImport(_Body):
    """Load a whole tenants.yaml. `apply: false` only reports; `apply: true` writes every
    changed team in one transaction, or nothing."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "yaml": "business_units:\n  finance:\n    groups: [g1]\n",
                    "apply": False,
                    "reason": "initial import",
                }
            ]
        },
    )
    yaml: str = Field(max_length=500_000, description="The text of a tenants.yaml.")
    apply: bool = Field(default=False, description="false: dry run; true: write.")
    reason: str = Field(
        default="", max_length=MAX_REASON, description="Required when `apply` is true."
    )
    acknowledge_warnings: list[str] = _ACK
    expected_revisions: dict[str, int | None] = Field(
        default_factory=dict,
        description="Team name -> `current_revision` from the dry run (null: team to create). "
        "Required with `apply: true` for every team that already exists and would change; "
        "any mismatch is 412 `revision_stale` and nothing is written. "
        "Create-only imports need none.",
    )


# --- response bodies ---------------------------------------------------------------------------


class FindingView(BaseModel):
    """One validation finding."""

    level: str = Field(description="`error` blocks a write; `warning` needs acknowledgement.")
    code: str = Field(description="Stable machine-readable code.")
    message: str = Field(description="What is wrong; never contains secrets.")
    team: str | None = Field(default=None, description="The team concerned, if any.")
    path: str | None = Field(default=None, description="Dotted path inside the team document.")


class BudgetImpact(BaseModel):
    """Reserved cost against the current and the proposed monthly budget."""

    reserved: float = Field(description="Estimated monthly cost reserved by existing resources.")
    current_budget: float | None = Field(description="Stored budget_monthly; null: unlimited.")
    new_budget: float | None = Field(description="Proposed budget_monthly; null: unlimited.")


class Impact(BaseModel):
    """What a change touches among existing resources."""

    resources: dict[str, int] = Field(
        description="Per environment, resources that still exist (not destroyed)."
    )
    budgets: dict[str, BudgetImpact] = Field(description="Per environment budget figures.")


class Diff(BaseModel):
    """Structural difference between two team documents, as dotted paths."""

    added: list[str] = Field(description="Paths present only in the newer document.")
    removed: list[str] = Field(description="Paths present only in the older document.")
    changed: list[str] = Field(description="Paths whose value differs.")


class ValidationReport(BaseModel):
    """Findings for a proposed team document. Nothing was written."""

    name: str = Field(description="Team name.")
    mode: str = Field(description="`create` when the team does not exist, else `update`.")
    valid: bool = Field(description="True when there are no error findings.")
    current_revision: int | None = Field(description="Stored revision; null for `create`.")
    findings: list[FindingView] = Field(description="All findings.")
    errors: int = Field(description="Number of error findings.")
    warnings: int = Field(description="Number of warning findings.")
    impact: Impact = Field(description="Effect on existing resources and budgets.")


class TeamView(BaseModel):
    """A team document with its revision. Operators only: contains target identifiers."""

    name: str = Field(description="Team name.")
    state: str = Field(description="`active` or `archived`.")
    revision: int = Field(description="Current revision; use as `If-Match`. 0 for the file source.")
    source: str = Field(description="`db` or `file` (read-only).")
    team: dict[str, Any] = Field(description="The business-unit spec.")
    created_at: str | None = Field(default=None, description="When the team was created.")
    created_by: str | None = Field(default=None, description="Operator who created it.")
    updated_at: str | None = Field(default=None, description="When it last changed.")
    updated_by: str | None = Field(default=None, description="Operator who last changed it.")
    findings: list[FindingView] | None = Field(
        default=None, description="Findings for this document, when computed."
    )
    impact: Impact | None = Field(default=None, description="Impact of the write just made.")


class FindingCounts(BaseModel):
    """Finding counts."""

    errors: int = Field(description="Error findings.")
    warnings: int = Field(description="Warning findings.")


class BudgetUse(BaseModel):
    """One environment's reserved cost against its budget."""

    reserved: float = Field(description="Estimated monthly cost reserved by existing resources.")
    monthly_budget: float | None = Field(description="budget_monthly; null: unlimited.")


class TeamSummary(BaseModel):
    """One team in the listing; no target identifiers."""

    name: str = Field(description="Team name.")
    state: str = Field(description="`active` or `archived`.")
    revision: int = Field(description="Current revision. 0 for the file source.")
    source: str = Field(description="`db` or `file`.")
    groups: int = Field(description="Number of groups with access.")
    environments: list[str] = Field(description="Environment names.")
    resources: int = Field(description="Resources that still exist (not destroyed).")
    budgets: dict[str, BudgetUse] = Field(description="Per environment, reserved vs budget.")
    findings: FindingCounts = Field(description="Counts for the current document.")


class TeamList(BaseModel):
    """All teams."""

    items: list[TeamSummary] = Field(description="Teams, sorted by name.")
    source: str = Field(description="`db` or `file`: where the mapping comes from.")


class RevisionSummary(BaseModel):
    """One entry of a team's history."""

    revision: int = Field(description="Revision number, from 1.")
    actor: str = Field(description="Operator who made the change.")
    at: str = Field(description="UTC timestamp.")
    reason: str = Field(description="The reason given.")
    state: str = Field(description="`active` or `archived` after this revision.")
    action: str = Field(description="create, update, revert, archive, unarchive or import.")
    change: Diff = Field(description="Difference from the previous revision.")


class RevisionPage(BaseModel):
    """A page of a team's history, newest first."""

    items: list[RevisionSummary] = Field(description="Revisions in this page.")
    next_before: int | None = Field(
        description="Pass as `before` for the next page; null on the last page."
    )


class RevisionView(RevisionSummary):
    """One revision with its document."""

    team: dict[str, Any] = Field(description="The document as of this revision.")


class ImportEntry(BaseModel):
    """What an import does to one team."""

    name: str = Field(description="Team name.")
    action: str = Field(description="`create`, `update` or `unchanged`.")
    revision: int | None = Field(description="Revision written; null for a dry run or no change.")
    current_revision: int | None = Field(
        description="Stored revision when the report was made; null for `create`. Send it back "
        "in `expected_revisions` to apply."
    )
    findings: list[FindingView] = Field(description="Findings for this team.")
    errors: int = Field(description="Error findings.")
    warnings: int = Field(description="Warning findings.")
    impact: Impact | None = Field(description="Effect on existing resources; null for `create`.")


class ImportReport(BaseModel):
    """Result of an import."""

    apply: bool = Field(description="The request's `apply`.")
    applied: bool = Field(description="True when anything was written.")
    teams: list[ImportEntry] = Field(description="One entry per team in the text.")
    findings: list[FindingView] = Field(description="File-level findings.")
    errors: int = Field(description="Error findings across the file and every team.")
    warnings: int = Field(description="Warning findings across the file and every team.")


# --- helpers -----------------------------------------------------------------------------------


def _view(f: Finding) -> dict:
    return {"level": f.level, "code": f.code, "message": f.message, "team": f.team, "path": f.path}


def _views(findings: list[Finding]) -> list[dict]:
    return [_view(f) for f in findings]


def require_db() -> None:
    if not tenants.db_source():
        raise OperationError(
            409,
            "team writes need FORGEAPI_TENANTS_SOURCE=db; switch the source or edit the "
            "tenants file",
            "tenants_source_file",
            next_action="switch_tenants_source",
        )


def _contract_findings() -> dict[str, list[dict]]:
    """Pattern contract findings already known (cached by earlier checks); never computed here."""
    return policy.default_findings()


def _catalog() -> dict | None:
    try:
        return catalog.load()
    except (catalog.CatalogError, OSError, AttributeError, TypeError):
        return None


def _flatten(value: Any, prefix: str, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        if not value and prefix:
            out[prefix] = {}
        for key, item in value.items():
            _flatten(item, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
        out[prefix] = {}  # presence marker; items below
        for item in value:
            out[f"{prefix}[{item}]"] = True
    else:
        out[prefix] = value


def diff(old: dict | None, new: dict) -> dict:
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    _flatten(old or {}, "", before)
    _flatten(new, "", after)
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "changed": sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
    }


def _others(con, name: str) -> dict[str, dict]:
    return {t["name"]: t["doc"] for t in team_store.list_all(con) if t["name"] != name}


def _resources_by_team(con) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for r in team_store.all_resources(con):
        grouped.setdefault(r.get("business_unit"), []).append(r)
    return grouped


def _review(con, name: str, doc: Any, stored: dict | None, others=None):
    """(findings, impact) of `doc` as the team's next document."""
    resources = team_store.resources(con, name)
    previous = stored["doc"] if stored else None
    findings = team_check.check_team(
        name,
        doc,
        catalog=_catalog(),
        current_resources=resources,
        previous=previous,
        other_teams=_others(con, name) if others is None else others,
        contract_findings=_contract_findings(),
    )
    safe = doc if isinstance(doc, dict) else (previous or {})
    return findings, team_check.impact(previous, safe, resources)


def _gate(findings: list[Finding], acknowledged: list[str], *, name: str | None = None) -> None:
    errors = [f for f in findings if f.level == "error"]
    if errors:
        raise OperationError(
            422,
            {"message": "the team document has errors", "findings": _views(findings)},
            "team_invalid",
            next_action="revise_team",
        )
    warnings = sorted({f.code for f in findings if f.level == "warning"})
    missing = [code for code in warnings if code not in set(acknowledged)]
    if missing:
        raise OperationError(
            409,
            {
                "message": "warnings must be acknowledged",
                "warning_codes": missing,
                "findings": _views([f for f in findings if f.level == "warning"]),
            },
            "warnings_not_acknowledged",
            next_action="acknowledge_warnings",
        )


def _revision_number(if_match: str | None) -> int | None:
    if if_match is None:
        raise OperationError(
            428, "send If-Match with the revision you read", "if_match_required",
            next_action="send_if_match",
        )  # fmt: skip
    text = if_match.strip().removeprefix("W/").strip('"')
    return int(text) if text.isdigit() else -1


def _stored(con, name: str, if_match: str | None = None, *, check: bool = True) -> dict:
    stored = team_store.get(con, name)
    if stored is None:
        raise HTTPException(404, "team not found")
    if check and _revision_number(if_match) != stored["revision"]:
        raise OperationError(
            412,
            f"team is at revision {stored['revision']}; re-read it and retry",
            "revision_stale",
            next_action="reread_team",
        )
    return stored


def _write(con, actor, name, doc, state, reason, action, stored, findings=None, impact=None):
    change = diff(stored["doc"] if stored else None, doc)
    row = team_store.write(
        con, actor, name, doc, state, reason, {"action": action, **change}, stored
    )
    return _team_view(row, "db", findings, impact)


def _team_view(row: dict, source: str, findings=None, impact=None) -> dict:
    return {
        "name": row["name"],
        "state": row["state"],
        "revision": row["revision"],
        "source": source,
        "team": row["doc"],
        "created_at": row.get("created_at"),
        "created_by": row.get("created_by"),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
        "findings": _views(findings) if findings is not None else None,
        "impact": impact,
    }


# --- reads -------------------------------------------------------------------------------------


def _docs(con) -> list[dict]:
    """Teams from the active source, as rows (file source: revision 0, state active)."""
    if tenants.db_source():
        return team_store.list_all(con)
    units = tenants.raw_mapping().get("business_units") or {}
    return [
        {"name": n, "revision": 0, "state": "active", "doc": d if isinstance(d, dict) else {}}
        for n, d in sorted(units.items())
    ]


def list_teams() -> dict:
    source = "db" if tenants.db_source() else "file"
    with ledger.connect(write=False) as con:
        rows = _docs(con)
        grouped = _resources_by_team(con)
        others = {r["name"]: r["doc"] for r in rows}
        items = []
        for row in rows:
            doc, resources = row["doc"], grouped.get(row["name"], [])
            findings = team_check.check_team(
                row["name"],
                doc,
                catalog=_catalog(),
                other_teams=others,
                contract_findings=_contract_findings(),
            )
            errors, warnings = team_check.counts(findings)
            envs = doc.get("environments") or {}
            items.append(
                {
                    "name": row["name"],
                    "state": row["state"],
                    "revision": row["revision"],
                    "source": source,
                    "groups": len(team_check._group_sets(doc)),
                    "environments": sorted(envs),
                    "resources": len(resources),
                    "budgets": {
                        env: {
                            "reserved": team_check._reserved(resources, env),
                            "monthly_budget": (spec or {}).get("budget_monthly"),
                        }
                        for env, spec in sorted(envs.items())
                    },
                    "findings": {"errors": errors, "warnings": warnings},
                }
            )
    return {"items": items, "source": source}


def get_team(name: str) -> dict:
    source = "db" if tenants.db_source() else "file"
    with ledger.connect(write=False) as con:
        rows = {r["name"]: r for r in _docs(con)}
        if name not in rows:
            raise HTTPException(404, "team not found")
        row = rows[name]
        findings = team_check.check_team(
            name,
            row["doc"],
            catalog=_catalog(),
            other_teams={n: r["doc"] for n, r in rows.items()},
            contract_findings=_contract_findings(),
        )
    return _team_view(row, source, findings)


def validate(body: TeamValidate) -> dict:
    with ledger.connect(write=False) as con:
        stored = team_store.get(con, body.name) if tenants.db_source() else None
        findings, impact = _review(con, body.name, body.team, stored)
    errors, warnings = team_check.counts(findings)
    return {
        "name": body.name,
        "mode": "update" if stored else "create",
        "valid": errors == 0,
        "current_revision": stored["revision"] if stored else None,
        "findings": _views(findings),
        "errors": errors,
        "warnings": warnings,
        "impact": impact,
    }


def revisions(name: str, before: int | None, limit: int) -> dict:
    with ledger.connect(write=False) as con:
        if team_store.get(con, name) is None:
            raise HTTPException(404, "team not found")
        rows = team_store.revisions(con, name, before, limit + 1)
    page = rows[:limit]
    return {
        "items": [_revision_summary(r) for r in page],
        "next_before": page[-1]["revision"] if len(rows) > limit else None,
    }


def _revision_summary(row: dict) -> dict:
    summary = row["summary"]
    return {
        "revision": row["revision"],
        "actor": row["actor"],
        "at": row["at"],
        "reason": row["reason"],
        "state": row["state"],
        "action": summary["action"],
        "change": {k: summary[k] for k in ("added", "removed", "changed")},
    }


def get_revision(name: str, number: int) -> dict:
    with ledger.connect(write=False) as con:
        row = team_store.revision(con, name, number)
    if row is None:
        raise HTTPException(404, "team revision not found")
    return {**_revision_summary(row), "team": row["doc"]}


# --- writes ------------------------------------------------------------------------------------


def create(actor: str, body: TeamCreate) -> dict:
    require_db()
    with ledger.connect() as con:
        if team_store.get(con, body.name):
            raise OperationError(409, "team already exists", "team_exists", next_action="update")
        findings, impact = _review(con, body.name, body.team, None)
        _gate_errors_only(findings)
        return _write(
            con,
            actor,
            body.name,
            body.team,
            "active",
            body.reason,
            "create",
            None,
            findings,
            impact,
        )


def _gate_errors_only(findings: list[Finding]) -> None:
    _gate([f for f in findings if f.level != "warning"], [])


def replace(actor: str, name: str, body: TeamReplace, if_match: str | None) -> dict:
    require_db()
    _revision_number(if_match)
    with ledger.connect() as con:
        stored = _stored(con, name, if_match)
        findings, impact = _review(con, name, body.team, stored)
        _gate(findings, body.acknowledge_warnings)
        _require_change(stored, body.team)
        return _write(
            con, actor, name, body.team, stored["state"], body.reason, "update", stored,
            findings, impact,
        )  # fmt: skip


def _require_change(stored: dict, doc: dict) -> None:
    if stored["doc"] == doc:
        raise OperationError(
            409, "the document equals the current revision", "team_unchanged",
            next_action="none",
        )  # fmt: skip


def revert(actor: str, name: str, body: TeamRevert, if_match: str | None) -> dict:
    require_db()
    _revision_number(if_match)
    with ledger.connect() as con:
        stored = _stored(con, name, if_match)
        target = team_store.revision(con, name, body.revision)
        if target is None:
            raise HTTPException(404, "team revision not found")
        findings, impact = _review(con, name, target["doc"], stored)
        _gate(findings, body.acknowledge_warnings)
        _require_change(stored, target["doc"])
        return _write(
            con, actor, name, target["doc"], stored["state"], body.reason, "revert", stored,
            findings, impact,
        )  # fmt: skip


def archive(actor: str, name: str, body: TeamArchive, if_match: str | None) -> dict:
    require_db()
    _revision_number(if_match)
    with ledger.connect() as con:
        stored = _stored(con, name, if_match)
        if stored["state"] == "archived":
            raise OperationError(409, "team is already archived", "team_archived")
        live = len(team_store.resources(con, name))
        if live and not body.force_archive:
            raise OperationError(
                409,
                f"team still has {live} resource(s) that are not destroyed; destroy them or "
                "send force_archive. Archived teams take no new intents but can destroy.",
                "team_has_resources",
                next_action="destroy_resources_or_force",
            )
        return _write(
            con, actor, name, stored["doc"], "archived", body.reason, "archive", stored,
        )  # fmt: skip


def unarchive(actor: str, name: str, body: TeamUnarchive, if_match: str | None) -> dict:
    require_db()
    _revision_number(if_match)
    with ledger.connect() as con:
        stored = _stored(con, name, if_match)
        if stored["state"] != "archived":
            raise OperationError(409, "team is not archived", "team_not_archived")
        findings, impact = _review(con, name, stored["doc"], stored)
        _gate_errors_only(findings)
        return _write(
            con, actor, name, stored["doc"], "active", body.reason, "unarchive", stored,
            findings, impact,
        )  # fmt: skip


# --- import ------------------------------------------------------------------------------------


def _plan_import(con, units: dict[str, Any]) -> list[dict]:
    stored = {t["name"]: t for t in team_store.list_all(con)}
    final = {n: t["doc"] for n, t in stored.items()} | {
        n: d for n, d in units.items() if isinstance(d, dict)
    }
    plan = []
    for name in sorted(units):
        doc, current = units[name], stored.get(name)
        findings, impact = _review(con, name, doc, current, others=final)
        action = "create" if current is None else "unchanged" if current["doc"] == doc else "update"
        errors, warnings = team_check.counts(findings)
        plan.append(
            {
                "name": name,
                "action": action,
                "revision": None,
                "current_revision": current["revision"] if current else None,
                "findings": findings,
                "errors": errors,
                "warnings": warnings,
                "impact": None if current is None else impact,
                "_doc": doc,
                "_stored": current,
            }
        )
    return plan


def _report(plan: list[dict], file_findings: list[Finding], applied: bool, apply: bool) -> dict:
    errors = sum(e["errors"] for e in plan) + team_check.counts(file_findings)[0]
    warnings = sum(e["warnings"] for e in plan) + team_check.counts(file_findings)[1]
    teams = [
        {**{k: v for k, v in e.items() if not k.startswith("_")}, "findings": _views(e["findings"])}
        for e in plan
    ]
    return {
        "apply": apply,
        "applied": applied,
        "teams": teams,
        "findings": _views(file_findings),
        "errors": errors,
        "warnings": warnings,
    }


def _check_expected(plan: list[dict], expected: dict[str, int | None]) -> None:
    """Refuse an import built on an older view of the teams than the stored one."""
    for entry in plan:
        current = entry["current_revision"]
        if current is None:
            continue
        if entry["name"] not in expected:
            if entry["action"] != "unchanged":
                raise OperationError(
                    428,
                    f"send expected_revisions for existing team {entry['name']} (from the dry run)",
                    "expected_revisions_required",
                    next_action="dry_run_then_send_expected_revisions",
                )
        elif expected[entry["name"]] != current:
            raise OperationError(
                412,
                f"team {entry['name']} is at revision {current}; run the import dry run again",
                "revision_stale",
                next_action="reread_team",
            )


def import_teams(actor: str, body: TeamImport) -> dict:
    """Dry run (read-only) or all-or-nothing apply of a tenants.yaml text."""
    if body.apply:
        require_db()
        if not body.reason.strip():
            raise HTTPException(422, "reason is required when apply is true")
    units, file_findings = team_check.parse_mapping(body.yaml)
    names = [n for n in units if not team_check.valid_name(n)]
    file_findings += [
        Finding("error", "name_invalid", f"team name {n!r} must match {NAME_PATTERN}", n)
        for n in names
    ]
    units = {n: d for n, d in units.items() if n not in names}
    if not body.apply:
        with ledger.connect(write=False) as con:
            plan = _plan_import(con, units)
        return _report(plan, file_findings, False, False)
    with ledger.connect() as con:
        plan = _plan_import(con, units)
        report = _report(plan, file_findings, False, True)
        changing = [e for e in plan if e["action"] != "unchanged"]
        _check_expected(plan, body.expected_revisions)
        # An unchanged team is not written, so its findings never block or need acknowledging.
        if team_check.counts(file_findings)[0] or any(e["errors"] for e in changing):
            raise OperationError(
                422,
                {"message": "the import has errors; nothing was written", "report": report},
                "team_invalid",
                next_action="revise_team",
            )
        warning_codes = sorted(
            {f.code for e in changing for f in e["findings"] if f.level == "warning"}
            | {f.code for f in file_findings if f.level == "warning"}
        )
        if missing := [c for c in warning_codes if c not in set(body.acknowledge_warnings)]:
            raise OperationError(
                409,
                {"message": "warnings must be acknowledged", "warning_codes": missing,
                 "report": report},
                "warnings_not_acknowledged",
                next_action="acknowledge_warnings",
            )  # fmt: skip
        for entry in plan:
            if entry["action"] == "unchanged":
                continue
            stored = entry["_stored"]
            view = _write(
                con, actor, entry["name"], entry["_doc"], stored["state"] if stored else "active",
                body.reason, "import", stored,
            )  # fmt: skip
            entry["revision"] = view["revision"]
        return _report(plan, file_findings, any(e["action"] != "unchanged" for e in plan), True)
