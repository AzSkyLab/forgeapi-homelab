"""Additive `change_summary` on Operation: computed in `public_operation` from `changes`."""

import pytest

from app.contracts import public_operation
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit

BASE_OP = {
    "id": "op_" + "a" * 32,
    "resource_id": "res_" + "b" * 32,
    "action": "deploy",
    "state": "planned",
    "pattern": "demo",
    "version": "v1.0.0",
    "commit": "c" * 40,
    "created_at": "2026-10-02T00:00:00Z",
    "updated_at": "2026-10-02T00:00:00Z",
    "plan_digest": "d" * 64,
    "outputs": None,
    "withheld_outputs": None,
    "error": None,
}

ZERO = {"create": 0, "update": 0, "delete": 0, "replace": 0, "destructive": False}


def summary_for(changes):
    return public_operation({**BASE_OP, "changes": changes})["change_summary"]


def test_change_summary_is_null_when_no_plan_exists():
    assert summary_for(None) is None


def test_change_summary_of_empty_plan_is_all_zero_and_not_destructive():
    assert summary_for([]) == ZERO


@pytest.mark.parametrize(
    "actions, field",
    [
        (["create"], "create"),
        (["update"], "update"),
        (["delete"], "delete"),
        (["delete", "create"], "replace"),
        (["create", "delete"], "replace"),
    ],
)
def test_change_summary_counts_each_action_shape(actions, field):
    summary = summary_for([{"address": "x", "type": "t", "actions": actions}])
    assert summary == {**ZERO, field: 1, "destructive": field in ("delete", "replace")}


def test_change_summary_of_mixed_plan_counts_each_kind_and_is_destructive():
    changes = [
        {"address": "a", "type": "t", "actions": ["create"]},
        {"address": "b", "type": "t", "actions": ["update"]},
        {"address": "c", "type": "t", "actions": ["delete"]},
        {"address": "d", "type": "t", "actions": ["create", "delete"]},
    ]
    assert summary_for(changes) == {
        "create": 1,
        "update": 1,
        "delete": 1,
        "replace": 1,
        "destructive": True,
    }


def test_change_summary_present_on_a_real_planned_operation(agent_client, recorded_dispatcher):
    op = submit(agent_client, "summary-http").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    assert planned["state"] == "planned"
    assert planned["change_summary"] == {**ZERO, "create": 1}
