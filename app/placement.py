"""Turn a pattern's variables plus a placement into (what the caller may set, what we inject)."""

import json
from typing import Any

from app.tenants import Placement, TenancyError


def sizes_for(config: dict[str, Any] | None, environment: str) -> dict[str, dict[str, Any]]:
    """Sizes the pattern offers in this environment: `sizing.<size>.<environment>.<variable>`."""
    sizing = (config or {}).get("sizing") or {}
    return {
        size: dict(per_env[environment])
        for size, per_env in sizing.items()
        if isinstance(per_env, dict) and isinstance(per_env.get(environment), dict)
    }


def shape(
    variables: list[dict[str, Any]],
    placement: Placement,
    config: dict[str, Any] | None,
    size: str | None,
    *,
    require_size: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(variables the caller may set, values the platform injects).

    A platform value is injected only when the pattern declares a variable of that exact name;
    that variable is then removed from what the caller sees."""
    unit, env = placement.unit, placement.environment
    available = sizes_for(config, env.name)
    sizing_defined = bool((config or {}).get("sizing"))
    if sizing_defined and size is None and require_size:
        raise TenancyError(422, f"name a size; {env.name} offers {list(available)}")
    if size is not None and size not in available:
        offered = list(available)  # the pattern's own order, smallest first by convention
        raise TenancyError(422, f"size {size!r} is not offered in {env.name}; choose {offered}")

    # For discovery (no size chosen yet) every sized variable is hidden, whatever the size.
    sized = available.get(size) or {k: None for values in available.values() for k in values}
    candidates = {**unit.inject, **env.network, "environment": env.name, **sized}
    declared = {v["name"] for v in variables}
    injected = {name: value for name, value in candidates.items() if name in declared}

    visible = []
    for variable in variables:
        if variable["name"] in injected:
            continue
        if variable["name"] == "location" and unit.regions_allowed:
            allowed = json.dumps(list(unit.regions_allowed))
            variable = {
                **variable,
                "required": variable["required"] and unit.region_default is None,
                "default": unit.region_default or variable["default"],
                "validations": [
                    *variable["validations"],
                    {
                        "condition": f"contains({allowed}, var.location)",
                        "error_message": f"location must be one of {list(unit.regions_allowed)} "
                        f"for {unit.name}.",
                    },
                ],
            }
        visible.append(variable)
    return visible, injected
