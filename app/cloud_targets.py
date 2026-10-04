"""Platform-owned cloud placement, pinned to each resource and hidden from callers."""

from app.contracts import OperationError
from app.tenants import Placement, TenancyError

IDENTIFIERS = {"azure": "subscription_id", "aws": "aws_account_id", "gcp": "project_id"}


def select(where: Placement, cloud: str | None) -> dict[str, str] | None:
    if cloud is None:
        if where.environment.targets:
            raise TenancyError(503, "pattern must declare its cloud for this environment")
        return None  # preserve the existing subscription-only mapping
    if cloud not in where.environment.targets:
        # The unit's environment simply does not offer this cloud: a caller-fixable refusal
        # (pick another environment or pattern), not a platform fault. Names no identifiers.
        raise OperationError(
            422,
            f"environment {where.environment.name} has no {cloud} target",
            "cloud_not_available",
            next_action="revise_intent",
        )
    target = where.environment.targets[cloud]
    required = {IDENTIFIERS[cloud], "region"}
    if (
        not isinstance(target, dict)
        or set(target) != required
        or any(not isinstance(v, str) or not v.strip() for v in target.values())
    ):
        raise TenancyError(503, "cloud target is not configured for this environment")
    return dict(target)


def deployable(env, cloud: str | None) -> bool:
    """Whether `select` could place a pattern of this cloud in this environment (ignoring
    malformed target configuration, which stays a 503). Cloud-less patterns only deploy where
    no cloud targets are configured, matching `select`."""
    return not env.targets if cloud is None else cloud in env.targets


def inject(variables: list[dict], target: dict[str, str] | None) -> tuple[list[dict], dict]:
    if target is None:
        return variables, {}
    # Requiring these inputs gives pattern authors a concrete provider configuration contract.
    # The trusted module must wire them to its provider, including AWS allowed_account_ids.
    if not set(target) <= {v["name"] for v in variables}:
        raise TenancyError(503, "pattern does not declare the required cloud target inputs")
    return [v for v in variables if v["name"] not in target], dict(target)


def unchanged(record: dict, cloud: str | None, target: dict | None, subscription=None):
    if (
        record.get("cloud") != cloud
        or record.get("cloud_target") != target
        or (not target and record.get("subscription_id") != subscription)
    ):
        raise OperationError(
            409,
            "resource cloud placement changed; operator reconciliation required",
            "placement_changed",
        )
