"""Terraform activities for the HTTP operation API. No automatic Terraform retries."""

import contextlib
import hashlib
import json
import logging
import re
import shutil
import time

from temporalio import activity

from app import ledger, terraform
from app.settings import settings

# Activity start-to-close timeouts are 10 and 30 minutes; SIGINT + terraform.GRACE must finish
# well inside those, so a deadline hit always surfaces as our own TerraformError, not a Temporal
# activity timeout.
PLAN_DEADLINE = 8 * 60
APPLY_DEADLINE = 28 * 60


def plan_path(op):
    return terraform.deployment_dir(op["resource_id"]) / "work" / "tfplan"


def owns(op):
    """True while this activity's running state is still the stored state. A late result after
    `ledger.interrupted` is ignored by `finish`, and the resource may hold a newer plan."""
    stored = ledger.get(op["id"])
    return stored is not None and stored["state"] == op["state"]


def fail(op, error, **extra):
    """Finish `failed` after dropping the plan file: it is not applyable and the resource is
    still reserved, so no other operation uses it. Never done for `uncertain`."""
    if owns(op):
        with contextlib.suppress(OSError):  # cleanup must never turn `failed` into `uncertain`
            plan_path(op).unlink(missing_ok=True)
    ledger.finish(op, "failed", error=error, **extra)


def digest(op):
    return hashlib.sha256(plan_path(op).read_bytes()).hexdigest()


def hidden_ids(op):
    identifiers = [
        value for key, value in (op.get("cloud_target") or {}).items() if key != "region"
    ]
    identifiers.append(op.get("subscription_id") or settings.azure_subscription_id)
    return [value for value in identifiers if value]


MIN_INJECTED_REDACTION = 6  # shorter values ("dev", "eu") would blank unrelated words


def redaction_ids(op):
    """Placement identifiers plus the string values of platform-injected inputs (resource group,
    subnet or VPC IDs, storage account names...): none of them may reach a stored summary."""
    found = hidden_ids(op)

    def walk(key, value):
        if isinstance(value, str):
            if key not in {"location", "region"} and len(value) >= MIN_INJECTED_REDACTION:
                found.append(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(k, v)
        elif isinstance(value, list):
            for v in value:
                walk(key, v)

    walk(None, op.get("injected") or {})
    return list(dict.fromkeys(found))


# --- Failure diagnostics ---------------------------------------------------------------------
# Terraform runs with safe_logs, so its first error is otherwise lost. Only a TerraformError's
# message is used (never other exception text) and it is sanitized before storage.

DIAGNOSTIC_MAX = 400
_AUTH_MARKERS = (
    "aadsts",
    "authenticat",
    "authoriz",
    "credential",
    "token",
    "unauthorized",
    "forbidden",
    "access denied",
    "accessdenied",
    "permission",
    "401",
    "403",
    "certificate",
    "signature",
    "invalidclienttokenid",
)
_REDACT = re.compile(
    r"https?://\S+|/subscriptions/\S+"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|(?<!\d)\d{12}(?!\d)",
    re.IGNORECASE,
)


def diagnostic(op, error) -> str | None:
    """Sanitized reason a Terraform command failed, or None for any other exception."""
    if not isinstance(error, terraform.TerraformError):
        return None
    text = str(error)
    command = re.match(r"terraform (\S+) failed:", text)
    if not command and not re.fullmatch(
        r"terraform \S+ exceeded its deadline( waiting for the provider cache)?", text
    ):
        return None  # only Terraform's own first error and our deadline text are shown
    if "Error acquiring the state lock" in text:
        return None  # the lock text names who holds it
    lowered = text.lower()
    if command and any(marker in lowered for marker in _AUTH_MARKERS):
        return (
            f"terraform {command.group(1)} failed: "
            "the cloud provider rejected the credentials or permissions"
        )
    for value in sorted(redaction_ids(op), key=len, reverse=True):
        text = re.sub(re.escape(value), "[redacted]", text, flags=re.IGNORECASE)
    return _REDACT.sub("[redacted]", text)[:DIAGNOSTIC_MAX]


# --- Attribute-level plan detail -------------------------------------------------------------
# Pure, Terraform-free helpers: paths and Terraform's own reason enum only, never a value (a
# value can carry a secret, an ID or placement data).

MAX_ATTRIBUTE_DEPTH = 3
MAX_CHANGED_ATTRIBUTES = 50


def render_path(path: list) -> str:
    """Dotted attribute path; a list index renders as `[n]`, e.g. ["rules", 0, "port"] ->
    "rules[0].port"."""
    parts: list[str] = []
    for segment in path:
        if isinstance(segment, int):
            parts[-1] += f"[{segment}]"
        else:
            parts.append(str(segment))
    return ".".join(parts)


def _diff_paths(before, after, unknown, prefix: tuple = ()) -> set[str]:
    """Dotted paths where `after` differs from `before`, or is unknown until apply. Depth-capped
    at MAX_ATTRIBUTE_DEPTH raw segments; a deeper difference reports its capped prefix."""
    if len(prefix) >= MAX_ATTRIBUTE_DEPTH:
        return {render_path(list(prefix[:MAX_ATTRIBUTE_DEPTH]))}
    if unknown is True:
        return {render_path(list(prefix))} if prefix else set()
    if isinstance(before, dict) or isinstance(after, dict) or isinstance(unknown, dict):
        keys = {k for node in (before, after, unknown) if isinstance(node, dict) for k in node}
        paths: set[str] = set()
        for key in keys:
            b = before.get(key) if isinstance(before, dict) else None
            a = after.get(key) if isinstance(after, dict) else None
            u = unknown.get(key) if isinstance(unknown, dict) else None
            if u or b != a:
                paths |= _diff_paths(b, a, u, (*prefix, key))
        return paths
    if isinstance(before, list) or isinstance(after, list) or isinstance(unknown, list):
        length = max(
            (len(node) for node in (before, after, unknown) if isinstance(node, list)),
            default=0,
        )
        paths = set()
        for index in range(length):
            b = before[index] if isinstance(before, list) and index < len(before) else None
            a = after[index] if isinstance(after, list) and index < len(after) else None
            u = unknown[index] if isinstance(unknown, list) and index < len(unknown) else None
            if u or b != a:
                paths |= _diff_paths(b, a, u, (*prefix, index))
        return paths
    return {render_path(list(prefix))} if before != after else set()


def _sensitive_paths(marks) -> tuple[set[str], bool]:
    """(dotted paths marked sensitive, depth-capped; True if the whole value is sensitive)."""
    if marks is True:
        return set(), True

    def walk(node, prefix: tuple) -> set[str]:
        if node is True:
            return {render_path(list(prefix[:MAX_ATTRIBUTE_DEPTH]))}
        if isinstance(node, dict):
            return {p for k, v in node.items() for p in walk(v, (*prefix, k))}
        if isinstance(node, list):
            return {p for i, v in enumerate(node) for p in walk(v, (*prefix, i))}
        return set()

    return walk(marks or {}, ()), False


def change_detail(raw_change: dict) -> dict:
    """Attribute-level detail for one `resource_changes` entry from `terraform show -json`:
    `changed_attributes`, `replace_paths`, `action_reason`, `sensitive_attributes`. Never a
    value, only paths and Terraform's own reason string."""
    change = raw_change["change"]
    actions = change["actions"]
    before = change.get("before")
    after = change.get("after")
    unknown = change.get("after_unknown") or {}
    if actions == ["delete"]:
        changed: list[str] = []
    elif actions == ["create"]:
        keys = set(after.keys()) if isinstance(after, dict) else set()
        if isinstance(unknown, dict):
            keys |= unknown.keys()
        changed = sorted(render_path([key]) for key in keys)
    else:
        changed = sorted(_diff_paths(before, after, unknown))
    changed = changed[:MAX_CHANGED_ATTRIBUTES]
    replace_paths = [render_path(path) for path in change.get("replace_paths") or []]
    before_marks, before_all = _sensitive_paths(change.get("before_sensitive"))
    after_marks, after_all = _sensitive_paths(change.get("after_sensitive"))
    if before_all or after_all:
        sensitive = list(changed)
    else:
        marks = before_marks | after_marks
        sensitive = sorted(path for path in changed if path in marks)
    return {
        "changed_attributes": changed,
        "replace_paths": replace_paths,
        "action_reason": raw_change.get("action_reason"),
        "sensitive_attributes": sensitive,
    }


_log = logging.getLogger("forgeapi.worker")
DRIFT_PLAN = "drift.tfplan"  # scratch only; never named `tfplan`, so it can never be applied


def planned_objects_of(raw: dict) -> list[dict]:
    """Managed objects left in state once the plan is applied, no-ops included. A `delete` and
    a `forget` (removed from state without destroying) both leave nothing behind."""
    return [
        {"address": c["address"], "type": c["type"]}
        for c in raw.get("resource_changes", [])
        if c.get("mode", "managed") == "managed"
        and c["change"]["actions"] not in (["delete"], ["forget"])
    ]


class _StateUnavailable(Exception):
    pass


def _state_resources(raw: dict) -> int:
    """Managed resources in a plan's prior state (`terraform show -json` of the plan)."""

    def count(module: dict) -> int:
        own = sum(1 for r in module.get("resources", []) if r.get("mode", "managed") == "managed")
        return own + sum(count(child) for child in module.get("child_modules", []))

    return count(((raw.get("prior_state") or {}).get("values") or {}).get("root_module") or {})


def run_drift_check(op, deadline):
    """Read-only: a refresh-only plan whose `resource_drift` is the verdict. `plan` never
    writes state (only applying a refresh-only plan would), so state is untouched; the scratch
    plan file is removed before the result is recorded. A held state lock fails the check at
    once (`-lock-timeout=0s`); it is never force-unlocked or retried."""
    resource_id, subscription = op["resource_id"], op.get("subscription_id")
    scratch = terraform.deployment_dir(resource_id) / "work" / DRIFT_PLAN
    run = dict(subscription_id=subscription, _unlock_once=False, safe_logs=True, deadline=deadline)
    result: dict = {}
    try:
        terraform.prepare(
            resource_id,
            op["source"],
            {**op["inputs"], **(op.get("injected") or {})},
            subscription,
            safe_logs=True,
            deadline=deadline,
        )
        terraform._run(
            resource_id,
            "plan", "-refresh-only", "-no-color", "-input=false", "-lock-timeout=0s",
            f"-out={DRIFT_PLAN}",
            **run,
        )  # fmt: skip
        raw = json.loads(terraform._run(resource_id, "show", "-json", DRIFT_PLAN, **run))
        drift = [
            {"address": c["address"], "type": c["type"], "actions": c["change"]["actions"]}
            for c in raw.get("resource_drift", [])
        ]
        if any(i.lower() in json.dumps(drift).lower() for i in redaction_ids(op)):
            raise ValueError("unsafe drift summary")
        # The refreshed prior state is empty when everything was deleted out of band, but then
        # `resource_drift` says so; neither means no state was loaded at all.
        known = ledger.resource(resource_id).get("managed_objects")
        if known and not drift and not _state_resources(raw):
            # Local-state pattern on a worker that never applied it: an empty state would
            # report everything gone, or nothing drifted. Never in_sync without state.
            raise _StateUnavailable
        result = {"state": "succeeded", "drift": drift}
    except _StateUnavailable:
        result = {
            "state": "failed",
            "error": "Terraform state is not available on this worker; run the check where the "
            "resource was applied, or use remote state",
            "diagnostic": None,
        }
    except Exception as error:
        # Class name only: messages can carry provider or auth text (receipts stay command-only).
        _log.warning("drift check %s failed: %s", op["id"], type(error).__name__)
        message = "Terraform drift check failed; inspect command receipts and pattern configuration"
        reason = diagnostic(op, error)
        if "Error acquiring the state lock" in str(error):
            message = "Terraform state is locked by another run; try the drift check again later"
            reason = None  # the lock text names who holds it
        result = {"state": "failed", "error": message, "diagnostic": reason}
    finally:
        with contextlib.suppress(OSError):
            scratch.unlink(missing_ok=True)
    state = result.pop("state")
    ledger.finish(op, state, **result)


@activity.defn
def run_operation_phase(operation_id: str, phase: str) -> bool:
    """Run only the specific phase scheduled by Temporal; never scan for work."""
    op = ledger.claim(operation_id, phase)
    if op is None:
        return False
    resource_id = op["resource_id"]
    subscription = op.get("subscription_id")
    variables = {**op["inputs"], **(op.get("injected") or {})}
    planning = op["state"] == "planning"
    deadline = time.monotonic() + (PLAN_DEADLINE if planning else APPLY_DEADLINE)
    if op["action"] == "drift_check":
        run_drift_check(op, deadline)
        return True
    try:
        if planning:
            terraform.prepare(
                resource_id,
                op["source"],
                variables,
                subscription,
                safe_logs=True,
                deadline=deadline,
            )
            args = ["plan", "-no-color", "-out=tfplan"]
            if op["action"] == "destroy":
                args.append("-destroy")
            terraform._run(
                resource_id,
                *args,
                subscription_id=subscription,
                _unlock_once=False,
                safe_logs=True,
                deadline=deadline,
            )
            raw = json.loads(
                terraform._run(
                    resource_id,
                    "show",
                    "-json",
                    "tfplan",
                    subscription_id=subscription,
                    _unlock_once=False,
                    safe_logs=True,
                    deadline=deadline,
                )
            )
            changes = [
                {
                    "address": c["address"],
                    "type": c["type"],
                    "actions": c["change"]["actions"],
                    **change_detail(c),
                }
                for c in raw.get("resource_changes", [])
                if c["change"]["actions"] != ["no-op"]
            ]
            # Terraform's refresh (already part of planning; no extra run) found these resources'
            # real state differs from its recorded state. `changes` already plans around them.
            drift = [
                {"address": c["address"], "type": c["type"], "actions": c["change"]["actions"]}
                for c in raw.get("resource_drift", [])
            ]
            # Objects that exist in state once this plan is applied, no-ops included. Private.
            planned_objects = planned_objects_of(raw)
            summary = json.dumps(changes + drift + planned_objects).lower()
            if any(identifier.lower() in summary for identifier in redaction_ids(op)):
                raise ValueError("unsafe plan summary")
            ledger.finish(
                op,
                "planned",
                plan_digest=digest(op),
                changes=changes,
                drift=drift,
                planned_objects=planned_objects,
            )
        else:
            if not plan_path(op).is_file() or digest(op) != op["plan_digest"]:
                fail(op, "saved plan missing or changed; submit a new intent")
                return True
            # No fallback planning, automatic unlock or retry. Never apply a different plan.
            try:
                terraform._run(
                    resource_id,
                    "apply",
                    "-no-color",
                    "tfplan",
                    subscription_id=subscription,
                    _unlock_once=False,
                    safe_logs=True,
                    deadline=deadline,
                )
            except terraform.TerraformError as error:
                reason = diagnostic(op, error)
                if "Saved plan is stale" in str(error):
                    fail(op, "saved plan is stale; submit a new intent", diagnostic=reason)
                    return True
                if "cannot be transferred between different Terraform versions" in str(error):
                    # A toolchain upgrade with plans pending: Terraform refuses before any change.
                    fail(
                        op,
                        "saved plan was made by a different Terraform version; submit a new intent",
                        diagnostic=reason,
                    )
                    return True
                if "Error acquiring the state lock" in str(error):
                    fail(
                        op,
                        "Terraform state is locked by another run; "
                        "an operator must inspect the lock",
                        diagnostic=reason,
                    )
                    return True
                raise
            plan_path(op).unlink()
            raw = json.loads(
                terraform._run(
                    resource_id,
                    "output",
                    "-json",
                    subscription_id=subscription,
                    _unlock_once=False,
                    safe_logs=True,
                    deadline=deadline,
                )
            )
            outputs, withheld = terraform.split_outputs(raw)
            # A pattern can output an ARM resource ID. It must not disclose placement IDs.
            identifiers = hidden_ids(op)
            if identifiers:
                for name in list(outputs):
                    if any(
                        value and value.lower() in json.dumps(outputs[name]).lower()
                        for value in identifiers
                    ):
                        withheld.append(name)
                        del outputs[name]
            json.dumps(outputs, allow_nan=False)
            if op["action"] == "destroy" and owns(op):
                shutil.rmtree(plan_path(op).parent / ".terraform", ignore_errors=True)
            ledger.finish(op, "succeeded", outputs=outputs, withheld_outputs=withheld)
    except Exception as error:
        # A nonzero apply may have partially changed resources. Never infer rollback or retry.
        if op["state"] == "applying":
            ledger.finish(
                op,
                "uncertain",
                error="execution outcome requires operator reconciliation",
                diagnostic=diagnostic(op, error),
            )
        else:
            fail(
                op,
                "Terraform planning failed; inspect command receipts and pattern configuration",
                diagnostic=diagnostic(op, error),
            )
    return True


@activity.defn
def mark_operation_uncertain(operation_id: str, phase: str) -> None:
    ledger.interrupted(operation_id, phase)
