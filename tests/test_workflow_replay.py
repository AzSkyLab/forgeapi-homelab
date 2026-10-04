"""Replay completed operation phase histories captured from the current worker."""

import asyncio
import base64
import json
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from app.operation_workflow import OperationPhaseWorkflow


@pytest.mark.parametrize(
    ("filename", "phase", "activities", "timeouts", "outcomes"),
    [
        (
            "plan_history.json",
            "plan",
            ["run_operation_phase"],
            ["600s"],
            ["EVENT_TYPE_ACTIVITY_TASK_COMPLETED"],
        ),
        (
            "apply_success_history.json",
            "apply",
            ["run_operation_phase"],
            ["1800s"],
            ["EVENT_TYPE_ACTIVITY_TASK_COMPLETED"],
        ),
        (
            "apply_activity_failure_history.json",
            "apply",
            ["run_operation_phase", "mark_operation_uncertain"],
            ["1800s", "30s"],
            ["EVENT_TYPE_ACTIVITY_TASK_FAILED", "EVENT_TYPE_ACTIVITY_TASK_COMPLETED"],
        ),
    ],
)
def test_completed_operation_history_replays(filename, phase, activities, timeouts, outcomes):
    fixture = json.loads((Path(__file__).parent / "fixtures/recovery" / filename).read_text())
    events = fixture["history"]["events"]
    assert events[-1]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"
    started = events[0]["workflowExecutionStartedEventAttributes"]
    assert json.loads(base64.b64decode(started["input"]["payloads"][1]["data"])) == phase
    scheduled = [
        event["activityTaskScheduledEventAttributes"]
        for event in events
        if event["eventType"] == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
    ]
    assert [item["activityType"]["name"] for item in scheduled] == activities
    assert [item["startToCloseTimeout"] for item in scheduled] == timeouts
    assert scheduled[0]["retryPolicy"]["maximumAttempts"] == 1
    assert [
        event["eventType"]
        for event in events
        if event["eventType"]
        in {"EVENT_TYPE_ACTIVITY_TASK_FAILED", "EVENT_TYPE_ACTIVITY_TASK_COMPLETED"}
    ] == outcomes
    assert sum(event["eventType"] == "EVENT_TYPE_ACTIVITY_TASK_STARTED" for event in events) == len(
        activities
    )
    history = WorkflowHistory.from_json(fixture["workflow_id"], fixture["history"])
    asyncio.run(Replayer(workflows=[OperationPhaseWorkflow]).replay_workflow(history))
