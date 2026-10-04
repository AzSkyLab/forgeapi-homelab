"""A blocked refusal write must not stall unrelated requests on the same API loop."""

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from app.settings import settings
from tests.test_client import http_client as _http_client
from tests.test_operations import INTENT

MARKER = "audit-lock-secret"


@pytest.fixture
def live_server(recorded_dispatcher):
    yield from _http_client.__wrapped__(recorded_dispatcher)


def _get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        return response.status, json.load(response)


def _invalid_post(url):
    body = json.dumps({"pattern": "demo", "unknown": MARKER}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Idempotency-Key": "blocked-refusal"},
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_locked_refusal_does_not_block_health_on_same_event_loop(
    live_server, recorded_dispatcher, monkeypatch
):
    with ledger.connect():
        pass
    lock = sqlite3.connect(settings.data_dir / "operations.sqlite", timeout=5)
    lock.execute("BEGIN IMMEDIATE")
    entered = threading.Event()
    original = ledger.refused

    def recording_refusal(actor, action):
        entered.set()
        return original(actor, action)

    monkeypatch.setattr(ledger, "refused", recording_refusal)
    base = live_server.base_url.removesuffix("/v1")
    with ThreadPoolExecutor(max_workers=2) as pool:
        mutation = pool.submit(_invalid_post, base + "/v1/operations")
        try:
            assert entered.wait(timeout=3), "refusal write was not attempted"
            health = pool.submit(_get, base + "/healthz")
            assert health.result(timeout=1) == (200, {"status": "ok", "contract": "agent-v1"})
            assert not mutation.done(), "mutation answered before its refusal was durable"
        finally:
            lock.rollback()
            lock.close()
        status, result = mutation.result(timeout=5)
    assert status == 422
    assert result["error"]["code"] == "invalid_request"
    assert MARKER not in json.dumps(result)
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0] == 1
    assert not recorded_dispatcher.pending


def test_queued_status_and_events_read_committed_data_during_refusal_lock(
    live_server, recorded_dispatcher, monkeypatch
):
    base = live_server.base_url.removesuffix("/v1")
    op = live_server.submit(INTENT, "read-during-lock")
    operation_id = op["id"]
    urls = [
        base + f"{prefix}/operations/{operation_id}{suffix}"
        for prefix in ("", "/v1")
        for suffix in ("", "/events")
    ]
    committed = [_get(url) for url in urls]
    assert all(status == 200 for status, _ in committed)
    assert committed[0][1]["state"] == committed[2][1]["state"] == "queued"
    assert [event["outcome"] for event in committed[1][1]["items"]] == ["accepted"]
    saved = ledger.get(operation_id)
    pending = list(recorded_dispatcher.pending)
    assert pending == [(operation_id, "plan")]

    entered = threading.Event()
    original = ledger.refused

    def recording_refusal(actor, action):
        entered.set()
        return original(actor, action)

    monkeypatch.setattr(ledger, "refused", recording_refusal)
    lock = sqlite3.connect(settings.data_dir / "operations.sqlite", timeout=5)
    with ThreadPoolExecutor(max_workers=5) as pool:
        try:
            lock.execute("BEGIN IMMEDIATE")
            lock.execute(
                "UPDATE operations SET state='failed', body=? WHERE id=?",
                (json.dumps({**saved, "state": "failed", "error": MARKER}), operation_id),
            )
            lock.execute(
                "INSERT INTO events (operation_id, actor, action, outcome, timestamp) "
                "VALUES (?, 'local', ?, 'uncommitted', '2026-10-02T00:00:00Z')",
                (operation_id, MARKER),
            )
            mutation = pool.submit(_invalid_post, base + "/operations")
            assert entered.wait(timeout=3), "refusal write was not attempted"
            reads = [pool.submit(_get, url) for url in urls]
            assert [future.result(timeout=1.5) for future in reads] == committed
            assert not mutation.done(), "mutation answered before its refusal was durable"
        finally:
            lock.rollback()
            lock.close()
        status, result = mutation.result(timeout=5)

    assert status == 422
    assert result["error"]["code"] == "invalid_request"
    assert MARKER not in json.dumps(result)
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM resources").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0] == 1
    assert [_get(url) for url in urls] == committed
    assert list(recorded_dispatcher.pending) == pending


def test_refusal_storage_failure_is_sanitized_503(recorded_dispatcher, monkeypatch):
    def failed_refusal(_actor, _action):
        raise sqlite3.OperationalError(MARKER)

    monkeypatch.setattr(ledger, "refused", failed_refusal)
    response = TestClient(app).post(
        "/v1/operations",
        json={"pattern": "demo", "unknown": MARKER},
        headers={"Idempotency-Key": "storage-failure"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"
    assert response.json()["error"]["detail"] == "audit unavailable; request was not accepted"
    assert MARKER not in response.text
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert not recorded_dispatcher.pending
