"""Business units: who may deploy what, where. See docs/tenancy.md.

The mapping is a YAML file today. Everything else talks to the functions here, so it can move
to a database without touching the API."""

from dataclasses import dataclass, field
from typing import Any

import yaml

from app.settings import settings


class TenancyError(Exception):
    def __init__(self, status: int, message: Any):
        super().__init__(str(message))
        self.status, self.message = status, message


@dataclass(frozen=True)
class Caller:
    id: str
    groups: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Environment:
    name: str
    subscription_id: str | None
    groups: frozenset[str] = frozenset()  # if set, BU membership alone is not enough
    network: dict[str, Any] = field(default_factory=dict)
    budget_monthly: float | None = None  # estimated monthly cost ceiling; None = unlimited
    targets: dict[str, dict[str, str]] = field(default_factory=dict)
    protected_resource_types: frozenset[str] = frozenset()  # delete/replace of these is denied
    allow_destroy: bool = True  # False: `action: destroy` intents are denied in this environment


@dataclass(frozen=True)
class BusinessUnit:
    name: str
    groups: frozenset[str]
    inject: dict[str, Any]
    patterns: frozenset[str]
    regions_allowed: tuple[str, ...]
    region_default: str | None
    environments: dict[str, Environment]
    archived: bool = False  # db source only: no new intents or placement; destroys still work
    revision: int | None = None  # db source only: the team revision this unit was built from

    def includes(self, caller: Caller) -> bool:
        env_groups = frozenset().union(*(e.groups for e in self.environments.values()))
        return bool(caller.groups & (self.groups | env_groups))

    def can_deploy(self, caller: Caller, environment: Environment) -> bool:
        return bool(caller.groups & (environment.groups or self.groups))


@dataclass(frozen=True)
class Placement:
    unit: BusinessUnit
    environment: Environment


def db_source() -> bool:
    return settings.tenants_source == "db"


def enabled() -> bool:
    return db_source() or bool(settings.tenants_yaml) or settings.tenants_path is not None


def build_unit(
    name: str, spec: dict, archived: bool = False, revision: int | None = None
) -> BusinessUnit:
    regions = spec.get("regions") or {}
    return BusinessUnit(
        name=name,
        groups=frozenset(spec.get("groups") or []),
        inject=dict(spec.get("inject") or {}),
        patterns=frozenset(spec.get("patterns") or []),
        regions_allowed=tuple(regions.get("allowed") or []),
        region_default=regions.get("default"),
        environments={
            env: Environment(
                name=env,
                subscription_id=e.get("subscription_id"),
                groups=frozenset(e.get("groups") or []),
                network=dict(e.get("network") or {}),
                budget_monthly=e.get("budget_monthly"),
                targets=dict(e.get("targets") or {}),
                protected_resource_types=frozenset(e.get("protected_resource_types") or []),
                allow_destroy=e.get("allow_destroy", True),
            )
            for env, e in (spec.get("environments") or {}).items()
        },
        archived=archived,
        revision=revision,
    )


def raw_mapping() -> dict:
    """The file source's whole parsed mapping (never used in db mode)."""
    text = settings.tenants_yaml or settings.tenants_path.read_text()
    return yaml.safe_load(text) or {}


def load() -> dict[str, BusinessUnit]:
    if db_source():
        from app import team_store  # lazy: the ledger imports modules that import this one

        return {
            name: build_unit(name, doc, archived=state == "archived", revision=revision)
            for name, state, doc, revision in team_store.all_docs()
        }
    raw = raw_mapping()
    return {
        name: build_unit(name, spec)
        for name, spec in (raw.get("business_units") or {}).items()
    }


def _setting_groups(text: str) -> frozenset[str]:
    return frozenset(g.strip() for g in text.split(",") if g.strip())


def is_auditor(caller: Caller) -> bool:
    """Members of the mapping's top-level `auditors` groups may read every unit's audit events
    (and nothing else: no deployments, no changes). In db mode: `FORGEAPI_AUDITOR_GROUPS`."""
    if db_source():
        return bool(caller.groups & _setting_groups(settings.auditor_groups))
    if not enabled():
        return False
    return bool(caller.groups & frozenset(raw_mapping().get("auditors") or []))


def is_operator(caller: Caller) -> bool:
    """Members of the mapping's top-level `operators` groups may resolve an uncertain operation
    in any business unit and read the team administration API. In db mode the groups come only
    from `FORGEAPI_OPERATOR_GROUPS`, never from the database."""
    if db_source():
        return bool(caller.groups & _setting_groups(settings.operator_groups))
    if not enabled():
        return False
    return bool(caller.groups & frozenset(raw_mapping().get("operators") or []))


def units_for(caller: Caller) -> list[BusinessUnit]:
    return [unit for unit in load().values() if unit.includes(caller)]


def select_unit(caller: Caller, requested: str | None) -> BusinessUnit:
    mine = {unit.name: unit for unit in units_for(caller)}
    if not mine:
        raise TenancyError(403, "you are not a member of any business unit with access to this API")
    if requested is None:
        if len(mine) > 1:
            raise TenancyError(422, f"name a business_unit; you belong to {sorted(mine)}")
        return next(iter(mine.values()))
    if requested not in mine:
        raise TenancyError(403, f"you are not a member of business unit {requested!r}")
    return mine[requested]


def place(
    caller: Caller,
    unit: BusinessUnit,
    environment: str | None,
    pattern: str,
    *,
    allow_archived: bool = False,
) -> Placement:
    """Where this caller's deployment of this pattern goes, or why it may not. An archived
    team (db source) takes no new work; callers handling existing resources, reads and destroys
    pass `allow_archived`."""
    if unit.archived and not allow_archived:
        raise TenancyError(
            409, f"business unit {unit.name} is archived; existing resources can still be destroyed"
        )
    if pattern not in unit.patterns:
        raise TenancyError(403, f"pattern {pattern!r} is not available to {unit.name}")
    if environment is None:
        raise TenancyError(422, f"name an environment; {unit.name} has {sorted(unit.environments)}")
    if environment not in unit.environments:
        has = sorted(unit.environments)
        raise TenancyError(422, f"{unit.name} has no {environment!r} environment; it has {has}")
    env = unit.environments[environment]
    if not unit.can_deploy(caller, env):
        raise TenancyError(403, f"you may not deploy to {unit.name}/{environment}")
    return Placement(unit, env)
