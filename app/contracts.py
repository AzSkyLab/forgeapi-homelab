"""Agent-native intent and operation contracts. No cloud SDK types cross this boundary."""

import json
import math
import re
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from app.settings import settings


class OperationError(HTTPException):
    """HTTPException carrying a machine-readable `reason` and, for a blocking resource
    conflict, the operation that is blocking it. `error_response` reads these via getattr,
    so a raise without them behaves exactly as before."""

    def __init__(self, status_code, detail, reason=None, *, operation_id=None, next_action=None):
        super().__init__(status_code, detail)
        self.reason = reason
        self.operation_id = operation_id
        self.next_action = next_action


LABEL_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")


def check_labels(value):
    for key, text in (value or {}).items():
        if not LABEL_KEY.fullmatch(key):
            raise ValueError("label keys must match ^[a-z][a-z0-9_.-]{0,62}$")
        if len(text) > 128 or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in text):
            raise ValueError("label values: at most 128 characters, no control characters")
    return value


def check_inputs(value):
    try:
        json.dumps(value, allow_nan=False)
    except ValueError:
        raise ValueError("inputs contain a non-finite number") from None
    return value


class InputRef(BaseModel):
    """Take one input's value from another resource's output."""

    model_config = ConfigDict(extra="forbid")

    resource_id: str = Field(pattern=r"^res_[0-9a-f]{32}$", description="Resource to read from.")
    output: str = Field(
        pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$",
        description="Name of a non-sensitive output of that resource.",
    )


class Intent(BaseModel):
    """What an agent wants: deploy a pattern, or destroy an existing resource."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "pattern": "local-file",
                    "version": "v1.0.0",
                    "business_unit": "platform",
                    "environment": "dev",
                    "inputs": {"filename": "agent.txt", "content": "hello from an agent"},
                }
            ]
        },
    )

    action: Literal["deploy", "destroy"] = Field(
        default="deploy",
        description="`deploy` applies the pattern; `destroy` tears down an existing `resource_id`.",
    )
    resource_id: str | None = Field(
        default=None,
        pattern=r"^res_[0-9a-f]{32}$",
        description="Existing resource to target; omit to create a new one. Required for destroy.",
    )
    pattern: str = Field(
        min_length=1, description="Registered pattern name; see GET /v1/agent for what's available."
    )
    version: str | None = Field(
        default=None, description="Pattern tag to pin; omit for the pattern's default version."
    )
    expected_commit: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{40}$",
        description="Commit last seen for this pattern/version; a moved tag is refused with "
        "reason revision_moved instead of silently resolving to something new.",
    )
    business_unit: str | None = Field(
        default=None,
        description="Owning business unit; required when tenancy is enabled and the caller "
        "belongs to more than one.",
    )
    environment: str | None = Field(
        default=None, description="Target environment within the business unit."
    )
    size: str | None = Field(
        default=None, description="Named size from the pattern's sizing table, if it has one."
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Pattern input variables. Platform-injected placement variables are "
        "rejected if set here.",
    )

    labels: dict[str, str] | None = Field(
        default=None,
        max_length=16,
        description="Optional metadata for finding resources: up to 16 entries, keys matching "
        "`^[a-z][a-z0-9_.-]{0,62}$`, values up to 128 characters without control characters. "
        "Never passed to Terraform; do not put secrets here. On update, omit to keep the "
        "resource's labels or send a map to replace them; not allowed with destroy.",
    )

    input_refs: dict[str, InputRef] | None = Field(
        default=None,
        max_length=16,
        description="Optional: input variable name -> `{resource_id, output}`; the value is "
        "copied from that ready resource's output when the intent is accepted (a snapshot; it "
        "does not follow later changes). The key must not also be in `inputs`. The source must "
        "be visible to you, in the same business unit and environment. An update must resend "
        "the refs it still wants; not allowed with destroy.",
    )

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, value):
        return check_labels(value)

    @field_validator("inputs")
    @classmethod
    def finite_inputs(cls, value):
        return check_inputs(value)

    @model_validator(mode="after")
    def destroy_targets_existing(self):
        if self.action == "destroy" and not self.resource_id:
            raise ValueError("destroy requires resource_id")
        return self


INTENT_FIELDS = (
    "action",
    "resource_id",
    "pattern",
    "version",
    "expected_commit",
    "business_unit",
    "environment",
    "size",
    "inputs",
)
OPTIONAL_INTENT_FIELDS = (
    "labels",
    "input_refs",
)  # omitted from the material when unset, keeping old keys


class Promote(BaseModel):
    """Create the same pattern, version and commit in another environment."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"environment": "prod", "inputs": {"content": "prod"}}]},
    )

    environment: str = Field(
        min_length=1,
        description="Target environment; must differ from the source's unless the business "
        "unit differs.",
    )
    business_unit: str | None = Field(
        default=None, description="Target business unit; defaults to the source's."
    )
    size: str | None = Field(
        default=None,
        min_length=1,
        description="Size in the target environment. Defaults to the source's size, which is "
        "refused (422, naming the offered sizes) when the target environment does not offer it.",
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Overrides merged over the source's caller inputs. Platform-injected "
        "placement variables are rejected here as on any intent.",
    )
    input_refs: dict[str, InputRef] | None = Field(
        default=None,
        max_length=16,
        description="References in the target environment. Required for every input the "
        "source took from a reference: those cannot cross environments.",
    )
    labels: dict[str, str] | None = Field(
        default=None,
        max_length=16,
        description="Labels merged over the source's; `promoted_from` is always set.",
    )

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, value):
        return check_labels(value)

    @field_validator("inputs")
    @classmethod
    def finite_inputs(cls, value):
        return check_inputs(value)


class Upgrade(BaseModel):
    """Move a ready resource to another version of its own pattern."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"version": "v1.1.0"}]}
    )

    version: str = Field(min_length=1, description="Pattern tag to move the resource to.")
    expected_commit: str | None = Field(
        default=None,
        min_length=1,
        description="Commit the tag was reviewed at. If the tag now resolves elsewhere the "
        "upgrade is refused 409 with reason revision_moved.",
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Overrides merged over the resource's stored caller inputs, e.g. an input "
        "the new version requires. Platform-injected variables are rejected as on any intent.",
    )
    labels: dict[str, str] | None = Field(
        default=None, max_length=16, description="Labels merged over the resource's."
    )

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, value):
        return check_labels(value)

    @field_validator("inputs")
    @classmethod
    def finite_inputs(cls, value):
        return check_inputs(value)


def canonical_intent(body: Intent) -> dict:
    """Freeze the v1 key identity, including default-expanded fields."""
    material = body.model_dump()
    for name in OPTIONAL_INTENT_FIELDS:
        if material[name] is None:
            del material[name]
    if set(material) - set(OPTIONAL_INTENT_FIELDS) != set(INTENT_FIELDS):
        raise RuntimeError(
            "Intent fields changed; define explicit canonicalization before accepting"
        )
    return material


class Execute(BaseModel):
    """Execute a saved plan exactly as reviewed. Executing twice with the same digest is safe;
    a stale or wrong digest is refused with reason plan_digest_mismatch."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"plan_digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}
            ]
        },
    )
    plan_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Digest of the saved plan to execute; must equal the operation's current "
        "plan_digest exactly.",
    )


class Reconcile(BaseModel):
    """An operator's recorded verdict on an `uncertain` operation, after inspecting Terraform
    state and the cloud provider directly. This never runs Terraform itself."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "outcome": "succeeded",
                    "reason": "Verified in the cloud console that the resource exists "
                    "and matches the plan.",
                }
            ]
        },
    )
    outcome: Literal["succeeded", "failed"] = Field(
        description="Operator's verdict after inspecting Terraform state and the provider "
        "themselves."
    )
    reason: str = Field(
        min_length=1, max_length=500, description="Human-readable justification; never parsed."
    )


TERMINAL = {"succeeded", "failed", "uncertain"}


class Change(BaseModel):
    """One planned Terraform change, as summarized from `terraform plan`."""

    address: str = Field(description="Terraform resource address.")
    type: str = Field(description="Terraform resource type.")
    actions: list[str] = Field(
        description='Terraform plan actions for this address, e.g. ["create"] or '
        '["delete", "create"] for a replace.'
    )
    changed_attributes: list[str] | None = Field(
        default=None,
        description="Dotted attribute paths whose value differs between before and after, or "
        "is unknown until apply; never the values themselves. A list index renders as [n] "
        "(e.g. tags.env, rules[0].port). Depth-capped at 3 segments and 50 entries per resource. "
        "Null on an operation stored before this field existed.",
    )
    replace_paths: list[str] | None = Field(
        default=None,
        description="Dotted attribute paths (same rendering as changed_attributes) that forced "
        "this replacement, from Terraform's own replace_paths; empty if this change is not a "
        "replace, or Terraform gave none. Null on an operation stored before this field existed.",
    )
    action_reason: str | None = Field(
        default=None,
        description="Terraform's own machine-readable reason for this change, e.g. "
        '"replace_because_cannot_update" or "delete_because_no_resource_config"; null when '
        "Terraform gave none, or on an operation stored before this field existed.",
    )
    sensitive_attributes: list[str] | None = Field(
        default=None,
        description="Entries of changed_attributes that Terraform marks sensitive; names only, "
        "so a reviewer knows a secret changes without seeing it. Null on an operation stored "
        "before this field existed.",
    )


class ChangeSummary(BaseModel):
    create: int = Field(description="Planned changes that create a new resource.")
    update: int = Field(description="Planned changes that update a resource in place.")
    delete: int = Field(description="Planned changes that delete a resource outright.")
    replace: int = Field(description="Planned changes that delete and recreate a resource.")
    destructive: bool = Field(description="True if this plan deletes or replaces anything.")


class Operation(BaseModel):
    """A durable record of one plan-and-apply lifecycle: queued, planned, applied, and its
    outcome. Poll `self` until `terminal`, following `next_action`."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "op_0123456789abcdef0123456789abcdef",
                    "resource_id": "res_0123456789abcdef0123456789abcdef",
                    "action": "deploy",
                    "state": "planned",
                    "pattern": "local-file",
                    "version": "v1.0.0",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "created_at": "2026-01-15T12:00:00+00:00",
                    "updated_at": "2026-01-15T12:00:05+00:00",
                    "plan_digest": (
                        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                    ),
                    "changes": [
                        {"address": "local_file.this", "type": "local_file", "actions": ["create"]}
                    ],
                    "drift": [],
                    "change_summary": {
                        "create": 1,
                        "update": 0,
                        "delete": 0,
                        "replace": 0,
                        "destructive": False,
                    },
                    "outputs": None,
                    "withheld_outputs": None,
                    "error": None,
                    "terminal": False,
                    "next_action": "execute_with_plan_digest",
                    "poll_after_seconds": None,
                    "links": {
                        "self": "/v1/operations/op_0123456789abcdef0123456789abcdef",
                        "events": "/v1/operations/op_0123456789abcdef0123456789abcdef/events",
                        "execute": "/v1/operations/op_0123456789abcdef0123456789abcdef/execute",
                        "discard": "/v1/operations/op_0123456789abcdef0123456789abcdef/discard",
                        "reconcile": "/v1/operations/op_0123456789abcdef0123456789abcdef/reconcile",
                        "resource": "/v1/resources/res_0123456789abcdef0123456789abcdef",
                    },
                }
            ]
        }
    )

    id: str = Field(description="Operation identifier.")
    resource_id: str = Field(description="Resource this operation acts on.")
    action: Literal["deploy", "destroy", "drift_check"] = Field(
        description="Action submitted for this operation. `drift_check` (capability "
        "`drift_checks`) is a read-only refresh-only plan: it never applies, ends `succeeded` "
        "with `drift`, and is left out of `GET /operations` unless asked for with "
        "`action=drift_check` or `include_checks=true`."
    )
    state: Literal[
        "queued",
        "planning",
        "planned",
        "apply_queued",
        "applying",
        "succeeded",
        "failed",
        "uncertain",
    ] = Field(description="Current lifecycle state; see `next_action` for what to do about it.")
    pattern: str = Field(description="Pattern this operation uses.")
    version: str | None = Field(description="Pattern version pinned at acceptance.")
    commit: str | None = Field(description="Pattern commit pinned at acceptance.")
    created_at: str = Field(description="When the operation was accepted.")
    updated_at: str = Field(description="When the operation last changed state.")
    plan_digest: str | None = Field(
        description="Digest of the saved plan; null until planning finishes. Pass back to "
        "`execute` unchanged."
    )
    changes: list[Change] | None = Field(
        description="Per-resource planned changes; null until a plan exists."
    )
    drift: list[Change] | None = Field(
        default=None,
        description="Resources whose real state differed from Terraform's recorded state when "
        "this plan was made, found by the refresh planning already performs (no extra "
        "Terraform run). Null until a plan exists on a server that records this field. "
        "Non-empty means something changed outside the API; the planned `changes` already "
        "account for it.",
    )
    change_summary: ChangeSummary | None = Field(
        default=None,
        description="Counts derived from `changes`: create/update/delete/replace and "
        "destructive. Null until a plan exists.",
    )
    outputs: dict[str, Any] | None = Field(
        description="Non-sensitive Terraform outputs from a successful apply; null otherwise."
    )
    withheld_outputs: list[str] | None = Field(
        description="Names of outputs withheld because they are sensitive; null until applied."
    )
    error: str | None = Field(
        description="Failure detail, if the operation failed; never raw exception text."
    )
    diagnostic: str | None = Field(
        default=None,
        description="Sanitized Terraform failure reason (the failing command and its first "
        "error) when the operation ended `failed` or `uncertain` because Terraform exited "
        "nonzero; placement IDs, URLs and credential errors are redacted. Null otherwise.",
    )
    plan_expires_at: str | None = Field(
        default=None,
        description="When this plan expires if not executed; set only while `planned` on a "
        "server with plan expiry enabled. After that, execute fails with `plan_expired`.",
    )
    terminal: bool = Field(
        description="True once this operation's outcome will not change further."
    )
    next_action: Literal[
        "execute_with_plan_digest",
        "done",
        "inspect_failure",
        "reconcile_with_operator",
        "poll",
    ] = Field(description="What the caller should do next.")
    poll_after_seconds: int | None = Field(
        description="Suggested delay before polling again; null when terminal or an action is "
        "required first."
    )
    links: dict[str, str] = Field(description="Related URLs for this operation.")


class OperationPage(BaseModel):
    """One page of operations, offset-paginated unless `before` was used."""

    items: list[Operation] = Field(description="Operations in this page.")
    next_offset: int | None = Field(
        description="Pass as `offset` for the next page; null when this page is the last or "
        "`before` was used instead."
    )
    next_before: str | None = Field(
        default=None,
        description="Pass as `before` for the next page; null when this page is the last.",
    )


APP_NAME = r"^[a-z][a-z0-9-]{1,40}$"
APP_STATES = (
    "planning_replicas",
    "awaiting_replica_approval",
    "applying_replicas",
    "planning_router",
    "awaiting_router_approval",
    "applying_router",
    "ready",
    "planning_router_destroy",
    "awaiting_router_destroy_approval",
    "destroying_router",
    "planning_replica_destroy",
    "awaiting_replica_destroy_approval",
    "destroying_replicas",
    "destroyed",
    "failed",
    "uncertain",
)
OUTPUT_NAME = r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$"


class AppReplica(BaseModel):
    """One replica of an app: a deployment of one pattern, usually one per cloud or region."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1, description="Registered pattern name for this replica.")
    version: str | None = Field(
        default=None, description="Pattern tag to pin; omit for the pattern's default version."
    )
    size: str | None = Field(
        default=None, description="Named size from the pattern's sizing table, if it has one."
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Pattern input variables. Platform-injected placement variables are "
        "rejected if set here.",
    )
    labels: dict[str, str] | None = Field(
        default=None,
        max_length=16,
        description="Optional labels for this replica; merged over the app's labels. The "
        "platform always sets `app` and `app_role`.",
    )

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, value):
        return check_labels(value)

    @field_validator("inputs")
    @classmethod
    def finite_inputs(cls, value):
        return check_inputs(value)


class ReplicaRef(BaseModel):
    """Take one router input from a replica's output."""

    model_config = ConfigDict(extra="forbid")

    replica: int = Field(
        ge=0, le=3, description="Zero-based index into the request's `replicas` list."
    )
    output: str = Field(
        pattern=OUTPUT_NAME,
        description="Name of a non-sensitive output of that replica, read when the replica "
        "has succeeded.",
    )


class AppRouter(AppReplica):
    """The router: a pattern deployed after every replica succeeded, whose inputs are copied
    from the replicas' outputs."""

    replica_refs: dict[str, ReplicaRef] = Field(
        min_length=1,
        max_length=16,
        description="Router input variable name -> the replica output that supplies it. The "
        "key must not also be in `inputs`.",
    )


class AppIntent(BaseModel):
    """Roll out a multi-cloud app in one request: 2 to 4 replicas, then a router behind a
    second approval. Every replica goes through the same validation, placement, budget and
    plan review as `POST /operations`."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "storefront",
                    "business_unit": "platform",
                    "environment": "dev",
                    "replicas": [
                        {"pattern": "ha-replica-aws", "version": "v1.0.0", "inputs": {}},
                        {"pattern": "ha-replica-azure", "version": "v1.0.0", "inputs": {}},
                    ],
                    "router": {
                        "pattern": "ha-router-aws",
                        "version": "v1.0.0",
                        "inputs": {},
                        "replica_refs": {
                            "primary_endpoint": {"replica": 0, "output": "endpoint"},
                            "secondary_endpoint": {"replica": 1, "output": "endpoint"},
                        },
                    },
                }
            ]
        },
    )

    name: str = Field(
        pattern=APP_NAME, description="App name; becomes the `app` label of every member."
    )
    business_unit: str | None = Field(
        default=None, description="Owning business unit, as for an intent."
    )
    environment: str | None = Field(
        default=None, description="Target environment within the business unit."
    )
    labels: dict[str, str] | None = Field(
        default=None,
        max_length=16,
        description="Optional labels applied to every member, as for an intent.",
    )
    replicas: list[AppReplica] = Field(
        min_length=2, max_length=4, description="Two to four replicas, planned together."
    )
    router: AppRouter = Field(
        description="The router, accepted automatically once every replica has succeeded."
    )

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, value):
        return check_labels(value)

    @model_validator(mode="after")
    def refs_point_at_replicas(self):
        for name, ref in self.router.replica_refs.items():
            if ref.replica >= len(self.replicas):
                raise ValueError(f"replica_refs.{name} names a replica that does not exist")
            if name in self.router.inputs:
                raise ValueError(f"input {name} is set both in inputs and replica_refs")
        return self


class AppApprove(BaseModel):
    """Approve the current gate of an app: name every planned operation at that gate with the
    exact digest you reviewed. A missing, extra or wrong entry refuses the whole approval."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "plan_digests": {
                        "op_0123456789abcdef0123456789abcdef": (
                            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                        )
                    }
                }
            ]
        },
    )

    plan_digests: dict[str, str] = Field(
        min_length=1,
        max_length=4,
        description="Operation ID -> its plan digest. At gate one, every replica; at gate two, "
        "the router (also after a failover). During teardown: the router's destroy at "
        "`awaiting_router_destroy_approval`, then every replica's destroy at "
        "`awaiting_replica_destroy_approval`.",
    )

    @field_validator("plan_digests")
    @classmethod
    def valid_digests(cls, value):
        for op_id, digest in value.items():
            if not re.fullmatch(r"op_[0-9a-f]{32}", op_id) or not re.fullmatch(
                r"[0-9a-f]{64}", digest
            ):
                raise ValueError("plan_digests maps op_<hex> operation IDs to 64-hex digests")
        return value


class AppFailover(BaseModel):
    """Make a different replica the active one. By convention the router's `replica_refs`
    inputs prefixed `primary_` name the active replica and those prefixed `secondary_` name the
    standby. Failover points every `primary_*` input at the chosen replica and every
    `secondary_*` input at the replica that was primary until now (with two replicas that is a
    swap; with more, `secondary_*` follows the previous primary). Other router inputs do not
    change. The change is a router update plan that someone approves with
    `POST /apps/{id}/approve`."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"primary": 1}]})

    primary: int = Field(
        ge=0,
        le=3,
        description="Zero-based index into the app's `members` (the request's `replicas`) of "
        "the replica that should become primary. Refused when it is already primary "
        "(`app_failover_noop`) or beyond the app's replicas (422).",
    )


class AppMember(BaseModel):
    """One operation that belongs to an app."""

    role: Literal["replica", "router", "router_destroy", "replica_destroy"] = Field(
        description="What this member is in the app; `router_destroy` and `replica_destroy` are "
        "the teardown operations listed in `teardown`."
    )
    index: int | None = Field(
        description="Position in the request's `replicas`; null for the router."
    )
    operation_id: str = Field(description="The member's operation; poll or review it there.")
    resource_id: str = Field(description="The resource the member creates.")
    state: str = Field(description="The member operation's current state.")
    plan_digest: str | None = Field(
        description="The member's plan digest once planned; approve with it."
    )


class App(BaseModel):
    """A multi-cloud app rollout: replicas, then a router. `state` is derived from the member
    operations; follow `next_action`."""

    id: str = Field(description="App identifier, `app_<hex>`.")
    name: str = Field(description="App name.")
    state: Literal[APP_STATES] = Field(
        description="Derived from the members' operation states: planning_replicas, "
        "awaiting_replica_approval, applying_replicas, planning_router, "
        "awaiting_router_approval, applying_router, ready; failed when a member failed or "
        "was discarded, or the rollout could not continue; uncertain when a member is "
        "uncertain (uncertain wins over failed). Teardown (`POST /apps/{id}/destroy`) adds "
        "planning_router_destroy, awaiting_router_destroy_approval, destroying_router, "
        "planning_replica_destroy, awaiting_replica_destroy_approval, destroying_replicas and "
        "destroyed. A failover returns the app to planning_router and "
        "awaiting_router_approval until its plan is approved and applied."
    )
    primary: int = Field(
        description="Index of the replica the router currently treats as primary (`primary_*` "
        "inputs); 0 for an app that never failed over unless its `replica_refs` say otherwise."
    )
    business_unit: str | None = Field(description="Owning business unit, if tenancy is on.")
    environment: str | None = Field(description="Environment, if tenancy is on.")
    created_at: str = Field(description="When the app was accepted.")
    updated_at: str = Field(description="When the app record last changed.")
    members: list[AppMember] = Field(description="The replica operations, in request order.")
    router: AppMember | None = Field(
        description="The router operation; null until the replicas have all succeeded."
    )
    error: str | None = Field(
        description="Why the rollout or teardown failed when that is not visible on a member "
        "(router refused, rollout timed out, a destroy refused); never raw exception text."
    )
    teardown: list[AppMember] | None = Field(
        default=None,
        description="The destroy operations of the current teardown, router first; null when "
        "no teardown was requested. Approve them with their `plan_digest` at the destroy gates.",
    )
    notice: str | None = Field(
        default=None,
        description="Set on acceptance: replicas were validated and budget-checked now; the "
        "router's budget is checked when it is accepted after the replicas succeed.",
    )
    next_action: Literal[
        "poll",
        "approve_with_plan_digests",
        "done",
        "inspect_failure",
        "reconcile_with_operator",
        "discard_planned_operations",
    ] = Field(
        description="What the caller should do next. `discard_planned_operations`: the app "
        "failed but other plans still reserve budget; `POST` the `discard` link."
    )
    poll_after_seconds: int | None = Field(
        description="Suggested delay before polling again; null when an action is required."
    )
    links: dict[str, str] = Field(
        description="Related URLs: `self`, `approve`, and `discard` when plans are left to discard."
    )


class AppPage(BaseModel):
    """One page of apps, oldest first."""

    items: list[App] = Field(description="Apps in this page.")
    next_after: str | None = Field(
        description="Pass as `after` for the next page; null when this page is the last."
    )


class ManagedObject(BaseModel):
    """One Terraform-managed object the resource holds."""

    address: str = Field(description="Terraform resource address.")
    type: str = Field(description="Terraform resource type.")


class Resource(BaseModel):
    """The caller's current view of one deployed resource, independent of any one operation."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "res_0123456789abcdef0123456789abcdef",
                    "state": "ready",
                    "pattern": "local-file",
                    "version": "v1.0.0",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "business_unit": "platform",
                    "environment": "dev",
                    "outputs": {"path": "example-output.txt"},
                    "withheld_outputs": [],
                    "latest_operation_id": "op_0123456789abcdef0123456789abcdef",
                    "updated_at": "2026-01-15T12:00:05+00:00",
                    "links": {
                        "self": "/v1/resources/res_0123456789abcdef0123456789abcdef",
                        "latest_operation": "/v1/operations/op_0123456789abcdef0123456789abcdef",
                    },
                }
            ]
        }
    )

    id: str = Field(description="Resource identifier; stable across every operation on it.")
    state: Literal["pending", "ready", "destroyed"] = Field(
        description="pending: accepted but never successfully applied. ready: last apply "
        "succeeded. destroyed: last destroy succeeded."
    )
    pattern: str = Field(description="Pattern last successfully applied, or intended if pending.")
    version: str | None = Field(description="Pattern version last successfully applied.")
    commit: str | None = Field(description="Pattern commit last successfully applied.")
    business_unit: str | None = Field(description="Owning business unit, if tenancy is enabled.")
    environment: str | None = Field(description="Target environment, if tenancy is enabled.")
    labels: dict[str, str] = Field(
        default_factory=dict, description="Caller labels on this resource; metadata only."
    )
    promoted_from: str | None = Field(
        default=None,
        description="Resource this one was promoted from (`POST /resources/{id}/promote`); "
        "the id only, never placement data. Requires capability `promotion`.",
    )
    input_refs: dict[str, InputRef] = Field(
        default_factory=dict, description="Output references this resource's inputs came from."
    )
    outputs: dict[str, Any] | None = Field(
        description="Non-sensitive Terraform outputs from the last successful apply."
    )
    withheld_outputs: list[str] | None = Field(
        description="Names of outputs withheld because they are sensitive."
    )
    latest_operation_id: str = Field(
        description="The in-flight, or else most recently finished, operation on this resource."
    )
    updated_at: str | None = Field(description="When this resource last changed.")
    cloud: str | None = Field(
        default=None,
        description="Cloud the pattern targets (aws, azure or gcp); null when the pattern "
        "declares none or on a resource stored before this field existed.",
    )
    region: str | None = Field(
        default=None,
        description="Region the resource was placed in by the platform; never an account, "
        "subscription or project ID. Null when no placement region applies or on a resource "
        "stored before this field existed.",
    )
    estimated_monthly_cost: float | None = Field(
        default=None,
        description="Estimated monthly cost of this resource from the pattern's pricing; null "
        "when the pattern declares none, the resource is destroyed, or the stored value is not "
        "a finite number.",
    )
    latest_version: str | None = Field(
        default=None,
        description="Highest semver tag of this resource's pattern that the caller may use; "
        "null when unknown (local pattern, destroyed or never-applied resource, pattern not "
        "usable by the caller, or the pattern repository was unreachable). Cached briefly. "
        "Requires capability `upgrade_detection`.",
    )
    upgrade_available: bool | None = Field(
        default=None,
        description="True when `latest_version` is newer than the applied `version`; null "
        "whenever `latest_version` is null.",
    )
    owned_by_caller: bool = Field(
        default=False,
        description="True when the calling identity is the one recorded as this resource's "
        "last actor. The actor identity itself is never exposed.",
    )
    created_at: str | None = Field(
        default=None,
        description="When the resource was first accepted; null on a resource stored before "
        "this field existed.",
    )
    managed_objects: list[ManagedObject] | None = Field(
        default=None,
        description="Terraform objects in state after the last successful apply, taken from "
        "that apply's saved plan (no-op objects included, data sources excluded); an empty "
        "list after a successful destroy. Null until a successful apply recorded it, and on a "
        "resource stored before this field existed.",
    )
    drift_status: Literal["in_sync", "drifted", "unknown"] | None = Field(
        default=None,
        description="Result of the latest drift check, or of the latest successful deploy or "
        "update (a fresh apply matches state, so `in_sync`). `drifted`: a refresh found "
        "something changed outside the API; see `drift`. `unknown`: the last check failed or "
        "an operator reconciled the last apply. Null when never checked, after destroy, and "
        "on a resource stored before this field existed. Requires capability `drift_checks`.",
    )
    drift_checked_at: str | None = Field(
        default=None,
        description="When `drift_status` was last set; null when it is null.",
    )
    drift: list[Change] | None = Field(
        default=None,
        description="Resources the latest drift check found changed outside the API (address, "
        "type and Terraform's actions only, as on an operation's `drift`); empty when in "
        "sync, null when unknown or never checked.",
    )
    links: dict[str, str] = Field(description="Related URLs for this resource.")


class ResourcePage(BaseModel):
    """One page of the caller's resource inventory, oldest resource ID first."""

    items: list[Resource] = Field(description="Resources in this page.")
    next_after: str | None = Field(
        description="Pass as `after` to fetch the next page; null when this page is the last."
    )


class Guardrails(BaseModel):
    """Plan guardrails for one environment, advertised before a plan is ever submitted."""

    allow_destroy: bool = Field(description="False when destroy plans are refused here.")
    protected_resource_types: list[str] = Field(
        description="Terraform resource types whose delete or replace is refused here."
    )


class Budget(BaseModel):
    """Estimated monthly spend headroom; the same sum submit enforces."""

    monthly_budget: float = Field(description="Estimated monthly cost ceiling.")
    reserved: float = Field(
        description="Estimated cost of non-destroyed resources plus legacy commitments."
    )
    available: float = Field(description="monthly_budget minus reserved, never below zero.")


class BusinessUnit(BaseModel):
    """A business unit the caller belongs to, and what it may do in this API."""

    name: str = Field(description="Business unit name.")
    archived: bool = Field(
        default=False,
        description="True when the team is archived: new intents are refused (409), while "
        "existing resources stay visible and can still be destroyed.",
    )
    environments: list[str] = Field(
        description="Environments this caller may deploy to in this unit."
    )
    regions: list[str] = Field(description="Regions allowed for this unit.")
    default_region: str | None = Field(description="Region used when an intent does not name one.")
    patterns: list[str] = Field(description="Patterns available to this unit.")
    deployable_patterns: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per deployable environment, the unit's patterns it can place: those whose "
        "cloud the environment targets, plus cloud-less patterns where no cloud targets are "
        "configured. A pattern missing here is refused 422 `cloud_not_available` there.",
    )
    guardrails: dict[str, Guardrails] = Field(
        default_factory=dict,
        description="Per deployable environment, the rules that will refuse a plan at execute.",
    )
    budgets: dict[str, Budget] = Field(
        default_factory=dict,
        description="Per deployable environment with a monthly budget, the estimated headroom "
        "admission enforces; environments without a budget are omitted.",
    )
    clouds: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per deployable environment, the sorted cloud names (aws, azure, gcp) it "
        "targets; names only, never account, subscription or project IDs. An environment "
        "without cloud targets maps to an empty list.",
    )


class DiscoveryLinks(BaseModel):
    """Entry points for the rest of the API; follow these rather than hard-coding paths."""

    patterns: str = Field(description="Template for GET {prefix}/patterns/{name}.")
    validation: str = Field(
        alias="validate", description="POST path to validate an intent without submitting it."
    )
    submit: str = Field(description="POST path to submit an intent.")
    operations: str = Field(description="GET path to list operations.")
    openapi: str = Field(description="This API's OpenAPI document.")
    resources: str | None = Field(
        default=None, description="GET path to list the resource inventory."
    )
    catalog: str | None = Field(default=None, description="GET path to list available patterns.")


class DiscoveryCaller(BaseModel):
    """What the platform knows about the caller, for deciding which screens to offer."""

    operator: bool = Field(
        description="True when the caller is an operator: may use the team administration API "
        "(`/admin/teams`) and reconcile uncertain operations."
    )


class Discovery(BaseModel):
    """Everything an agent needs to start: capabilities, available patterns, and the workflow
    and semantics it must follow. Read-only; never mutates anything."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "contract": "agent-v1",
                    "supported_api_major": 1,
                    "software_release": "1.0.0",
                    "capabilities": {
                        "idempotent_intents": True,
                        "exact_plan_execution": True,
                        "operation_events": True,
                        "stable_operation_pagination": True,
                        "discard_planned_operation": True,
                        "resource_inventory": True,
                        "operator_reconciliation": True,
                        "resource_filters": True,
                        "pattern_listing": True,
                        "guardrail_discovery": True,
                        "budget_discovery": True,
                    },
                    "patterns": ["local-file"],
                    "business_units": [
                        {
                            "name": "platform",
                            "environments": ["dev"],
                            "regions": ["eastus"],
                            "default_region": "eastus",
                            "patterns": ["local-file"],
                            "guardrails": {
                                "dev": {"allow_destroy": True, "protected_resource_types": []}
                            },
                            "budgets": {
                                "dev": {
                                    "monthly_budget": 100.0,
                                    "reserved": 60.0,
                                    "available": 40.0,
                                }
                            },
                        }
                    ],
                    "workflow": [
                        "describe_pattern",
                        "validate_intent",
                        "submit_intent",
                        "poll_until_planned",
                        "inspect_changes",
                        "execute_exact_plan",
                        "poll_outcome",
                    ],
                    "semantics": {
                        "dispatch": "202 acknowledges queued work; on dispatch_unconfirmed "
                        "retry the same request"
                    },
                    "links": {
                        "patterns": "/v1/patterns/{name}",
                        "validate": "/v1/intents/validate",
                        "submit": "/v1/operations",
                        "operations": "/v1/operations",
                        "openapi": "/v1/openapi.json",
                        "resources": "/v1/resources",
                        "catalog": "/v1/patterns",
                    },
                }
            ]
        }
    )

    contract: Literal["agent-v1"] = Field(
        description="Fixed contract identifier for this API family."
    )
    supported_api_major: Literal[1] = Field(
        default=1, description="Major version this server implements."
    )
    software_release: str | None = Field(
        default=None,
        description="Server build version, for diagnostics only; not a contract version.",
    )
    capabilities: dict[str, StrictBool] = Field(
        default_factory=dict, description="Feature flags this deployment supports."
    )
    caller: DiscoveryCaller | None = Field(
        default=None, description="The caller's role; absent from older servers."
    )
    patterns: list[str] = Field(description="Pattern names available to this caller.")
    business_units: list[BusinessUnit] = Field(
        description="Business units this caller belongs to, if tenancy is enabled."
    )
    workflow: list[str] = Field(description="Ordered steps an agent should follow end to end.")
    semantics: dict[str, str] = Field(
        description="Prose notes on idempotency, dispatch and uncertainty an agent must honor."
    )
    links: DiscoveryLinks = Field(description="Entry points for the rest of the API.")


class PatternSummary(BaseModel):
    """One available pattern; follow `links.self` for its schema."""

    name: str = Field(description="Pattern name.")
    cloud: str | None = Field(description="Cloud this pattern targets, if any.")
    links: dict[str, str] = Field(description="`self`: GET path for this pattern's description.")


class PatternList(BaseModel):
    """The patterns this caller may use, by name."""

    items: list[PatternSummary] = Field(description="Available patterns, sorted by name.")


class PatternDescription(BaseModel):
    """One pattern's schema and metadata, with any platform-injected inputs already removed."""

    name: str = Field(description="Pattern name.")
    cloud: str | None = Field(
        description="Cloud this pattern targets, if any; null when it manages no cloud resources."
    )
    version: str | None = Field(
        description="Version described; the pattern's default if none was requested."
    )
    commit: str | None = Field(description="Commit pinned to this version.")
    about: dict[str, Any] = Field(description="Pattern-authored free-form description.")
    input_schema: dict[str, Any] = Field(
        description="JSON Schema for this pattern's `inputs`, with platform-injected fields "
        "removed."
    )
    versions: list[str] = Field(description="Tags available for this pattern.")
    example: dict[str, Any] = Field(description="Example `inputs` payload for this pattern.")
    sizes: list[Any] = Field(
        description="Named sizes available, if the pattern declares a sizing table."
    )


class PatternCommit(BaseModel):
    commit: str = Field(description="Commit id.")
    subject: str = Field(description="Commit subject line only; no author information.")


class PatternInputChanges(BaseModel):
    added: list[dict[str, Any]] = Field(
        description="New inputs: `name`, `required`, `type`, optional `description`."
    )
    removed: list[str] = Field(description="Names of inputs that no longer exist.")
    changed: list[dict[str, Any]] = Field(
        description="`name` and `fields`: `required`/`type` as [old, new], and "
        "`default_changed` true when an optional input's default differs (values are never shown)."
    )


class PatternChanges(BaseModel):
    """Changelog between two tags of one pattern."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="Pattern name.")
    from_: str = Field(alias="from", description="Older tag.")
    to: str = Field(description="Newer tag.")
    from_commit: str = Field(description="Commit the older tag points at.")
    to_commit: str = Field(description="Commit the newer tag points at.")
    commits: list[PatternCommit] = Field(description="Newest first; at most 100.")
    inputs: PatternInputChanges = Field(description="Input-schema differences.")
    new_required_inputs: list[str] = Field(
        description="Inputs a caller must now supply that the older tag did not require."
    )


class PatternFinding(BaseModel):
    """One contract-check finding."""

    level: Literal["error", "warning", "info"] = Field(
        description="error: would be refused or break; warning: risky; info: for your awareness."
    )
    code: str = Field(description="Stable finding code.")
    message: str = Field(description="What was found and how to fix it. Never contains values.")
    file: str | None = Field(default=None, description="File inside the pattern, when known.")
    line: int | None = Field(default=None, description="1-based line, when known.")


class PatternCheck(BaseModel):
    """Static contract check of one pattern version (Terraform is not run)."""

    name: str = Field(description="Pattern name.")
    version: str | None = Field(description="Checked tag; null for an unversioned local pattern.")
    commit: str | None = Field(description="Commit the tag points at.")
    findings: list[PatternFinding] = Field(description="Every finding, errors first as found.")
    errors: int = Field(description="Number of error findings.")
    warnings: int = Field(description="Number of warning findings.")


class ValidationResult(BaseModel):
    """The result of schema-and-placement validation only; nothing is reserved or run."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "valid": True,
                    "validation_scope": "schema_and_placement",
                    "terraform_plan_performed": False,
                    "budget_reserved": False,
                    "pattern": "local-file",
                    "version": "v1.0.0",
                    "commit": "0123456789abcdef0123456789abcdef01234567",
                    "estimated_monthly_cost": 0.0,
                }
            ]
        }
    )

    valid: bool = Field(
        description="Always true for a response; a failed validation raises an error instead."
    )
    validation_scope: str = Field(description="What was checked: schema and placement only.")
    terraform_plan_performed: bool = Field(
        description="Always false; validation never runs Terraform."
    )
    budget_reserved: bool = Field(description="Always false; validation never reserves budget.")
    pattern: str | None = Field(description="Resolved pattern name.")
    version: str | None = Field(description="Resolved pattern version.")
    commit: str | None = Field(description="Resolved pattern commit.")
    estimated_monthly_cost: Any = Field(
        description="Estimated monthly cost, if the pattern declares pricing."
    )


class Event(BaseModel):
    """One append-only audit entry."""

    seq: int = Field(description="Monotonic sequence number; use as `after` for the next page.")
    operation_id: str | None = Field(description="Operation this event belongs to, if any.")
    actor: str = Field(description="Caller or system identity that caused this event.")
    action: str = Field(description="What was attempted, e.g. operation.create.")
    outcome: str = Field(description="What happened, e.g. accepted, refused, planned.")
    timestamp: str = Field(description="When this event was recorded.")


class EventPage(BaseModel):
    """One page of an operation's audit trail, oldest first."""

    items: list[Event] = Field(description="Events in this page.")
    next_after: int | None = Field(
        description="Pass as `after` for the next page; null when this page is the last."
    )


class ErrorDetail(BaseModel):
    code: str = Field(description="Frozen machine-readable error code; stable across releases.")
    detail: Any = Field(description="Human-readable explanation; never raw exception text.")
    next_action: str = Field(description="What the caller should do next.")
    operation_id: str | None = Field(
        default=None, description="The operation this error concerns, if any."
    )
    status_url: str | None = Field(
        default=None, description="Where to poll that operation's current state, if any."
    )
    reason: str | None = Field(
        default=None,
        description="Machine-readable sub-code narrowing `code` for a specific conflict or "
        "refusal. Omitted when no finer-grained reason applies.",
    )
    request_id: str | None = Field(
        default=None,
        description="Identifier correlating this error to the server's access log; echoes the "
        "response's X-Request-ID header.",
    )


class ErrorEnvelope(BaseModel):
    """Every error response is shaped `{"error": {...}}`; `code` and `reason` are frozen values."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": "conflict",
                        "detail": "a saved plan must match the current digest",
                        "next_action": "inspect_and_correct_request",
                        "reason": "plan_digest_mismatch",
                    }
                }
            ]
        }
    )

    error: ErrorDetail = Field(description="The error detail.")


def _change_summary(changes: list[dict] | None) -> dict | None:
    """Counts per `Change`; an entry with both create and delete actions counts as replace."""
    if changes is None:
        return None
    counts = {"create": 0, "update": 0, "delete": 0, "replace": 0}
    for change in changes:
        actions = set(change["actions"])
        if {"create", "delete"} <= actions:
            counts["replace"] += 1
        elif "create" in actions:
            counts["create"] += 1
        elif "delete" in actions:
            counts["delete"] += 1
        elif "update" in actions:
            counts["update"] += 1
    return {**counts, "destructive": bool(counts["delete"] or counts["replace"])}


def plan_expires_at(operation: dict) -> datetime | None:
    """When a planned operation expires, or None when expiry is off or it is not planned.
    Older plans without `planned_at` fall back to their last update."""
    if settings.plan_max_age_hours is None or operation["state"] != "planned":
        return None
    since = operation.get("planned_at") or operation["updated_at"]
    return datetime.fromisoformat(since) + timedelta(hours=settings.plan_max_age_hours)


def public_operation(operation: dict, prefix: str = "") -> dict:
    """Explicit allowlist: internal inputs, placement and subscription never reach the caller.

    `reconciliation` (an operator's reason for resolving an `uncertain` operation) is internal
    only and is deliberately never added to this allowlist."""
    state = operation["state"]
    next_action = {
        "planned": "execute_with_plan_digest",
        "succeeded": "done",
        "failed": "inspect_failure",
        "uncertain": "reconcile_with_operator",
    }.get(state, "poll")
    base = f"{prefix}/operations/{operation['id']}"
    return {
        **{
            k: operation.get(k)
            for k in (
                "id",
                "resource_id",
                "action",
                "state",
                "pattern",
                "version",
                "commit",
                "created_at",
                "updated_at",
                "plan_digest",
                "changes",
                "drift",
                "outputs",
                "withheld_outputs",
                "error",
                "diagnostic",
            )
        },
        "change_summary": _change_summary(operation.get("changes")),
        "plan_expires_at": expires.isoformat() if (expires := plan_expires_at(operation)) else None,
        "terminal": state in TERMINAL,
        "next_action": next_action,
        "poll_after_seconds": 2 if next_action == "poll" else None,
        "links": {
            "self": base,
            "events": f"{base}/events",
            "execute": f"{base}/execute",
            "discard": f"{base}/discard",
            "reconcile": f"{base}/reconcile",
            "resource": f"{prefix}/resources/{operation['resource_id']}",
        },
    }


def _cost(value):
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    try:
        return float(value) if ok and math.isfinite(value) else None
    except OverflowError:
        return None


def _region(resource: dict) -> str | None:
    """Placement region only; never an account, subscription or project ID."""
    target = resource.get("cloud_target") or {}
    injected = resource.get("injected") or {}
    value = target.get("region") or injected.get("region") or injected.get("location")
    return value if isinstance(value, str) and value else None


def public_resource(resource: dict, caller_id: str | None = None, prefix: str = "") -> dict:
    """Explicit allowlist: inputs, injected placement, source and actor never reach a caller.
    The actor is reduced to `owned_by_caller`; cost is the caller-visible estimate only."""
    base = f"{prefix}/resources/{resource['id']}"
    return {
        "id": resource["id"],
        "state": resource.get("state") or "pending",
        "pattern": resource.get("pattern"),
        "version": resource.get("version"),
        "commit": resource.get("commit"),
        "business_unit": resource.get("business_unit"),
        "environment": resource.get("environment"),
        "labels": resource.get("labels") or {},
        "promoted_from": resource.get("promoted_from"),
        "input_refs": resource.get("input_refs") or {},
        "outputs": resource.get("outputs"),
        "withheld_outputs": resource.get("withheld_outputs"),
        "latest_version": None,  # filled by the route; needs the caller's allowed patterns
        "upgrade_available": None,
        "latest_operation_id": resource["operation_id"],
        "updated_at": resource.get("updated_at"),
        "cloud": resource.get("cloud"),
        "region": _region(resource),
        # A destroyed resource costs nothing; its last estimate would mislead cost totals.
        "estimated_monthly_cost": None
        if resource.get("state") == "destroyed"
        else _cost(resource.get("estimated_monthly_cost")),
        "owned_by_caller": caller_id is not None and resource.get("actor") == caller_id,
        "created_at": resource.get("resource_created_at"),
        "managed_objects": resource.get("managed_objects"),
        "drift_status": resource.get("drift_status"),
        "drift_checked_at": resource.get("drift_checked_at"),
        "drift": resource.get("drift"),
        "links": {
            "self": base,
            "latest_operation": f"{prefix}/operations/{resource['operation_id']}",
        },
    }
