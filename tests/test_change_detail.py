"""Attribute-level plan detail: `changed_attributes`, `replace_paths`, `action_reason` and
`sensitive_attributes` on each planned change. Paths and Terraform's own reason enum only --
never an attribute VALUE, which can carry secrets, IDs or placement data.

The helper (`change_detail`) is pure and unit-tested here against crafted `resource_changes`
entries shaped like real `terraform show -json` output. The real-Terraform section at the bottom
verifies that shape against actual Terraform 1.15.9 output for the `local-file` example pattern.
"""

from pathlib import Path

from app.contracts import Operation, public_operation
from app.operation_activities import change_detail
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit


def rc(actions, before=None, after=None, **extra):
    """One crafted `resource_changes` entry."""
    change = {"actions": actions, "before": before, "after": after}
    change.update({k: v for k, v in extra.items() if k != "action_reason"})
    entry = {"address": "x.this", "type": "x", "change": change}
    if "action_reason" in extra:
        entry["action_reason"] = extra["action_reason"]
    return entry


def test_create_reports_top_level_keys_of_after_plus_unknown_keys():
    change = rc(
        ["create"],
        before=None,
        after={"name": "a", "size": 1},
        after_unknown={"id": True},
    )
    detail = change_detail(change)
    assert detail["changed_attributes"] == ["id", "name", "size"]
    assert detail["replace_paths"] == []
    assert detail["action_reason"] is None
    assert detail["sensitive_attributes"] == []


def test_delete_reports_no_changed_attributes():
    change = rc(["delete"], before={"name": "a", "size": 1}, after=None)
    detail = change_detail(change)
    assert detail["changed_attributes"] == []
    assert detail["replace_paths"] == []
    assert detail["sensitive_attributes"] == []


def test_update_of_nested_map_and_list_values_renders_dotted_paths_with_index_brackets():
    change = rc(
        ["update"],
        before={
            "tags": {"env": "dev", "owner": "a"},
            "rules": [{"port": 80}, {"port": 443}],
            "unchanged": "same",
        },
        after={
            "tags": {"env": "prod", "owner": "a"},
            "rules": [{"port": 8080}, {"port": 443}],
            "unchanged": "same",
        },
    )
    detail = change_detail(change)
    assert detail["changed_attributes"] == ["rules[0].port", "tags.env"]


def test_unknown_after_apply_value_is_reported_without_a_before_after_difference():
    change = rc(
        ["update"],
        before={"name": "a", "generated": "old-id"},
        after={"name": "a"},
        after_unknown={"generated": True},
    )
    detail = change_detail(change)
    assert detail["changed_attributes"] == ["generated"]


def test_depth_is_capped_at_three_segments_for_a_deeper_difference():
    change = rc(
        ["update"],
        before={"a": {"b": {"c": {"d": "old"}}}},
        after={"a": {"b": {"c": {"d": "new"}}}},
    )
    detail = change_detail(change)
    assert detail["changed_attributes"] == ["a.b.c"]


def test_changed_attributes_list_is_capped_at_fifty_entries():
    before = {f"k{i}": "old" for i in range(80)}
    after = {f"k{i}": "new" for i in range(80)}
    change = rc(["update"], before=before, after=after)
    detail = change_detail(change)
    assert len(detail["changed_attributes"]) == 50
    assert detail["changed_attributes"] == sorted(detail["changed_attributes"])


def test_replace_with_replace_paths_and_action_reason_are_surfaced():
    change = rc(
        ["delete", "create"],
        before={"content": "old", "id": "old-id"},
        after={"content": "new"},
        after_unknown={"id": True},
        replace_paths=[["content"]],
        action_reason="replace_because_cannot_update",
    )
    detail = change_detail(change)
    assert detail["replace_paths"] == ["content"]
    assert detail["action_reason"] == "replace_because_cannot_update"
    assert "content" in detail["changed_attributes"]


def test_sensitive_attribute_change_names_the_path_but_never_the_value():
    change = rc(
        ["update"],
        before={"password": "old-secret-value", "name": "a"},
        after={"password": "new-secret-value", "name": "a"},
        before_sensitive={"password": True},
        after_sensitive={"password": True},
    )
    detail = change_detail(change)
    assert detail["sensitive_attributes"] == ["password"]
    assert detail["changed_attributes"] == ["password"]
    blob = str(detail)
    assert "old-secret-value" not in blob
    assert "new-secret-value" not in blob


def test_whole_object_marked_sensitive_flags_every_changed_attribute():
    change = rc(
        ["update"],
        before={"a": "old", "b": "old"},
        after={"a": "new", "b": "new"},
        before_sensitive=True,
    )
    detail = change_detail(change)
    assert detail["sensitive_attributes"] == detail["changed_attributes"] == ["a", "b"]


def test_old_stored_operation_without_the_new_keys_serializes_with_nulls():
    legacy = {
        "id": "op_" + "a" * 32,
        "resource_id": "res_" + "b" * 32,
        "action": "deploy",
        "state": "planned",
        "pattern": "local-file",
        "version": "v1.0.0",
        "commit": "c" * 40,
        "created_at": "2026-01-15T12:00:00Z",
        "updated_at": "2026-01-15T12:00:00Z",
        "plan_digest": "d" * 64,
        "changes": [{"address": "local_file.this", "type": "local_file", "actions": ["create"]}],
        "outputs": None,
        "withheld_outputs": None,
        "error": None,
    }
    # public_operation forwards a stored change dict verbatim; a server that predates this
    # field never wrote these keys. The response boundary (the Operation model) is what fills
    # the nulls a caller actually sees.
    validated = Operation.model_validate(public_operation(legacy))
    change = validated.changes[0]
    assert change.changed_attributes is None
    assert change.replace_paths is None
    assert change.action_reason is None
    assert change.sensitive_attributes is None


# ---------------------------------------------------------------------------
# Real Terraform: local-file pattern, applied then resubmitted with changed content.
# ---------------------------------------------------------------------------


def test_real_terraform_replace_of_local_file_content_shows_replace_paths_and_action_reason(
    agent_client, recorded_dispatcher
):
    secret_content = "very-secret-content-value-should-not-leak"
    created = submit(
        agent_client,
        "change-detail-deploy",
        pattern="local-file",
        version=None,
        inputs={"filename": "change-detail.txt", "content": "original"},
    ).json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(created["links"]["self"]).json()
    assert planned["state"] == "planned", planned
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert recorded_dispatcher.run_next()
    done = agent_client.get(created["links"]["self"]).json()
    assert done["state"] == "succeeded", done
    assert Path(done["outputs"]["path"]).read_text() == "original"

    updated = submit(
        agent_client,
        "change-detail-replan",
        pattern="local-file",
        version=None,
        resource_id=created["resource_id"],
        inputs={"filename": "change-detail.txt", "content": secret_content},
    ).json()
    assert recorded_dispatcher.run_next()
    replanned = agent_client.get(updated["links"]["self"]).json()
    assert replanned["state"] == "planned", replanned
    (change,) = replanned["changes"]
    assert change["address"] == "local_file.this"
    assert set(change["actions"]) == {"create", "delete"}
    # Assert reality, not a guess: what Terraform 1.15.9 actually emits for a local_file whose
    # content changed (it cannot update content in place; id is derived from the content hash).
    assert "content" in change["replace_paths"]
    assert change["action_reason"] == "replace_because_cannot_update"
    assert "content" in change["changed_attributes"]

    response_text = agent_client.get(replanned["links"]["self"]).text
    assert secret_content not in response_text

    assert agent_client.post(replanned["links"]["discard"]).status_code == 200
