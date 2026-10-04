"""Drift visibility: `drift` on a planned Operation lists resources Terraform's refresh found
changed outside the API (from `resource_drift` in `show -json tfplan`), distinct from `changes`
(what this plan would do about it). No extra Terraform run: planning already refreshes.

Verified against real Terraform 1.15.9 with the `local-file` example pattern: for a `local_file`
resource, both deleting the file and editing its content out-of-band make the provider's refresh
report the object gone (`resource_drift` action `["delete"]`); the following plan then recreates
it (`resource_changes` action `["create"]`). A fresh (never-applied) first plan, and a re-plan
with nothing touched out-of-band, both have no `resource_drift` key at all in `show -json`.
"""

from pathlib import Path

from app import ledger, terraform
from app.contracts import Operation, public_operation
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit

LOCAL_FILE = {
    "pattern": "local-file",
    "version": None,
    "inputs": {"filename": "drift.txt", "content": "original"},
}

# `changed_attributes`/`sensitive_attributes` observed from real Terraform 1.15.9 for a fresh
# local_file create: every top-level attribute key of `after` (+ unknown keys), and
# `sensitive_content` is the provider's one sensitive-marked attribute regardless of its (null)
# value. `drift` entries keep their existing shape (no attribute detail is added to them).
CREATE = [
    {
        "address": "local_file.this",
        "type": "local_file",
        "actions": ["create"],
        "changed_attributes": [
            "content",
            "content_base64",
            "content_base64sha256",
            "content_base64sha512",
            "content_md5",
            "content_sha1",
            "content_sha256",
            "content_sha512",
            "directory_permission",
            "file_permission",
            "filename",
            "id",
            "sensitive_content",
            "source",
        ],
        "replace_paths": [],
        "action_reason": None,
        "sensitive_attributes": ["sensitive_content"],
    }
]
# Drift entries never get attribute detail computed; the Change model still serializes those
# fields as null (the same model is shared with `changes`).
DRIFT_DELETE = [
    {
        "address": "local_file.this",
        "type": "local_file",
        "actions": ["delete"],
        "changed_attributes": None,
        "replace_paths": None,
        "action_reason": None,
        "sensitive_attributes": None,
    }
]


def _why(op):
    """A failed plan explains itself: the error plus the tail of the Terraform log."""
    log = terraform.log_path(op["resource_id"])
    return op, log.read_text()[-3000:] if log.exists() else "no terraform.log"


def _deploy(agent_client, recorded_dispatcher, key):
    """Submit, plan, execute and apply a fresh local_file resource. Returns the succeeded op."""
    created = submit(agent_client, key, **LOCAL_FILE).json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(created["links"]["self"]).json()
    assert planned["state"] == "planned", planned
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert recorded_dispatcher.run_next()
    done = agent_client.get(created["links"]["self"]).json()
    assert done["state"] == "succeeded", done
    return done


def _replan(agent_client, recorded_dispatcher, resource_id, key):
    """Submit a new intent against an existing resource_id and run its plan phase."""
    submitted = submit(
        agent_client, key, **{**LOCAL_FILE, "resource_id": resource_id}
    ).json()
    assert recorded_dispatcher.run_next()
    return agent_client.get(submitted["links"]["self"]).json()


def test_deleted_file_out_of_band_shows_in_drift_and_a_recreate_in_changes(
    agent_client, recorded_dispatcher
):
    done = _deploy(agent_client, recorded_dispatcher, "drift-delete-deploy")
    out_path = Path(done["outputs"]["path"])
    assert out_path.is_file()
    out_path.unlink()  # out-of-band: something other than the API removed it

    replanned = _replan(
        agent_client, recorded_dispatcher, done["resource_id"], "drift-delete-replan"
    )
    assert replanned["state"] == "planned", _why(replanned)
    assert replanned["changes"] == CREATE
    assert replanned["drift"] == DRIFT_DELETE

    discard = agent_client.post(replanned["links"]["discard"])
    assert discard.status_code == 200, discard.text


def test_modified_file_out_of_band_also_shows_as_drift_delete_for_local_file(
    agent_client, recorded_dispatcher
):
    """local_file's refresh treats any out-of-band content change as the object having vanished
    (its `id` is the content hash), not an in-place update; this is what Terraform 1.15.9 emits,
    not a guess."""
    done = _deploy(agent_client, recorded_dispatcher, "drift-modify-deploy")
    Path(done["outputs"]["path"]).write_text("tampered out-of-band")

    replanned = _replan(
        agent_client, recorded_dispatcher, done["resource_id"], "drift-modify-replan"
    )
    assert replanned["state"] == "planned", _why(replanned)
    assert replanned["changes"] == CREATE
    assert replanned["drift"] == DRIFT_DELETE

    assert agent_client.post(replanned["links"]["discard"]).status_code == 200


def test_fresh_plan_with_no_out_of_band_change_has_empty_drift(agent_client, recorded_dispatcher):
    created = submit(agent_client, "drift-fresh").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(created["links"]["self"]).json()
    assert planned["state"] == "planned", planned
    assert planned["drift"] == []  # first-ever plan: nothing to have drifted from


def test_replan_with_nothing_touched_out_of_band_has_empty_drift(agent_client, recorded_dispatcher):
    done = _deploy(agent_client, recorded_dispatcher, "drift-noop-deploy")
    replanned = _replan(agent_client, recorded_dispatcher, done["resource_id"], "drift-noop-replan")
    assert replanned["state"] == "planned", _why(replanned)
    assert replanned["changes"] == []  # nothing changed, nothing to plan
    assert replanned["drift"] == []
    assert agent_client.post(replanned["links"]["discard"]).status_code == 200


def test_old_stored_operation_without_the_key_returns_null_drift():
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
        "changes": CREATE,
        "outputs": None,
        "withheld_outputs": None,
        "error": None,
        # no "drift" key at all: as if stored by a server that predates this field
    }
    assert Operation.model_validate(public_operation(legacy)).drift is None
    assert public_operation(legacy)["drift"] is None


def test_drift_entry_with_a_hidden_placement_id_fails_planning_like_changes_does(
    monkeypatch, tmp_path, agent_client, recorded_dispatcher
):
    """The same scan that keeps a hidden placement ID out of `changes` must also cover `drift`:
    a drifted resource whose address embeds the id must fail planning, never reach the ledger."""
    from app.settings import settings

    monkeypatch.setattr(settings, "azure_subscription_id", "hidden-subscription-id")
    fake = tmp_path / "terraform"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "if sys.argv[1] == 'init':\n"
        "    sys.exit(0)\n"
        "elif sys.argv[1] == 'plan':\n"
        "    open('tfplan', 'w').write('fake-plan')\n"  # so digest() would succeed if reached
        "    sys.exit(0)\n"
        "elif sys.argv[1] == 'show':\n"
        "    print(json.dumps({\n"
        "        'resource_changes': [],\n"
        "        'resource_drift': [{\n"
        "            'address': 'local_file.hidden-subscription-id',\n"
        "            'type': 'local_file',\n"
        "            'change': {'actions': ['update']},\n"
        "        }],\n"
        "    }))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "terraform_bin", str(fake))

    op = submit(agent_client, "drift-hidden-id").json()
    assert recorded_dispatcher.run_next()

    result = ledger.get(op["id"])
    assert result["state"] == "failed"
    assert result["plan_digest"] is None
    assert result["changes"] is None
    assert result.get("drift") is None  # never written: the raise happens before ledger.finish
