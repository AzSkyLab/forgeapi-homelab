"""Committed ledger reads do not reserve SQLite's single writer slot."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import ledger
from app.settings import settings


@pytest.mark.parametrize("method", ["get", "resource", "replay", "list", "events"])
def test_reads_complete_during_another_connection_write_transaction(method):
    intent = {"action": "deploy"}
    resolved = {"pattern": "demo", "version": "v1.0.0", "estimated_monthly_cost": 0}
    first = ledger.accept("local", "first", intent, resolved, None)
    second = ledger.accept("local", "second", intent, resolved, None)
    original_resource = ledger.resource(first["resource_id"])
    path = settings.data_dir / "operations.sqlite"

    def read():
        if method == "get":
            return ledger.get(first["id"])
        if method == "resource":
            return ledger.resource(first["resource_id"])
        if method == "replay":
            return ledger.replay("local", "first", intent)
        if method == "list":
            return ledger.list_operations("local", None, 0, 10, before=second["id"])
        return ledger.events(first["id"], 0, 10)

    writer = sqlite3.connect(path, timeout=5)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "UPDATE operations SET state='failed', body=? WHERE id=?",
            (json.dumps({**first, "state": "failed", "error": "uncommitted"}), first["id"]),
        )
        writer.execute(
            "UPDATE resources SET body=? WHERE id=?",
            (
                json.dumps({"id": first["resource_id"], "state": "uncommitted"}),
                first["resource_id"],
            ),
        )
        writer.execute(
            "INSERT INTO events (operation_id, actor, action, outcome, timestamp) "
            "VALUES (?, 'local', 'test', 'uncommitted', '2026-10-02T00:00:00Z')",
            (first["id"],),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(read)
            try:
                result = future.result(timeout=1.5)
                if method in {"get", "replay"}:
                    assert result == first
                elif method == "resource":
                    assert result == original_resource
                elif method == "list":
                    assert result == [first]
                else:
                    assert [event["outcome"] for event in result] == ["accepted"]
            finally:
                writer.rollback()  # Release the held lock even on the pre-fix timeout.
    finally:
        writer.close()


def test_first_read_initializes_a_new_ledger():
    path = settings.data_dir / "operations.sqlite"
    assert not path.exists()
    assert ledger.get("op_missing") is None
    assert path.exists()
