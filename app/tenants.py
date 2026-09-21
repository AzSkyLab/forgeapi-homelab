"""Business units: who may deploy what, where. See docs/tenancy.md.

The mapping is a YAML file today. Everything else talks to the functions here, so it can move
to a database without touching the API."""

from dataclasses import dataclass, field
from typing import Any

import yaml

from app.settings import settings


class TenancyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass(frozen=True)
class Caller:
    id: str
    groups: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Environment:
    name: str
    subscription_id: str
    groups: frozenset[str] = frozenset()  # if set, BU membership alone is not enough
    network: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BusinessUnit:
    name: str
    groups: frozenset[str]
    inject: dict[str, Any]
    patterns: frozenset[str]
    regions_allowed: tuple[str, ...]
    region_default: str | None
    environments: dict[str, Environment]

    def includes(self, caller: Caller) -> bool:
        env_groups = frozenset().union(*(e.groups for e in self.environments.values()))
        return bool(caller.groups & (self.groups | env_groups))

    def can_deploy(self, caller: Caller, environment: Environment) -> bool:
        return bool(caller.groups & (environment.groups or self.groups))


@dataclass(frozen=True)
class Placement:
    unit: BusinessUnit
    environment: Environment


def enabled() -> bool:
    return settings.tenants_path is not None


def load() -> dict[str, BusinessUnit]:
    raw = yaml.safe_load(settings.tenants_path.read_text()) or {}
    units = {}
    for name, spec in (raw.get("business_units") or {}).items():
        regions = spec.get("regions") or {}
        units[name] = BusinessUnit(
            name=name,
            groups=frozenset(spec.get("groups") or []),
            inject=dict(spec.get("inject") or {}),
            patterns=frozenset(spec.get("patterns") or []),
            regions_allowed=tuple(regions.get("allowed") or []),
            region_default=regions.get("default"),
            environments={
                env: Environment(
                    name=env,
                    subscription_id=e["subscription_id"],
                    groups=frozenset(e.get("groups") or []),
                    network=dict(e.get("network") or {}),
                )
                for env, e in (spec.get("environments") or {}).items()
            },
        )
    return units


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


def place(caller: Caller, unit: BusinessUnit, environment: str | None, pattern: str) -> Placement:
    """Where this caller's deployment of this pattern goes, or why it may not."""
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
