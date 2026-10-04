"""`?wait=` long-polls `GET /operations/{id}` without blocking the event loop or any threadpool
thread for the whole wait."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.main import app, caller_context
from app.tenants import Caller
from tests.test_operations import INTENT


@pytest.fixture
def agent_client(recorded_dispatcher):
    return TestClient(app)


def submit(client, key="wait-key", **updates):
    response = client.post(
        "/operations", json={**INTENT, **updates}, headers={"Idempotency-Key": key}
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_wait_zero_is_todays_behavior(agent_client):
    op = submit(agent_client)
    start = time.monotonic()
    response = agent_client.get(op["links"]["self"])
    elapsed = time.monotonic() - start
    assert response.status_code == 200
    assert response.json()["state"] == "queued"
    assert elapsed < 1


@pytest.mark.parametrize("state_setup", ["succeeded", "failed", "uncertain"])
def test_terminal_state_returns_immediately_even_with_wait(agent_client, state_setup):
    op = submit(agent_client, key=f"terminal-{state_setup}")
    claimed = ledger.claim(op["id"], "plan")
    ledger.finish(claimed, state_setup, error=None if state_setup == "succeeded" else "x")
    start = time.monotonic()
    response = agent_client.get(op["links"]["self"], params={"wait": 10})
    elapsed = time.monotonic() - start
    assert response.status_code == 200
    assert response.json()["state"] == state_setup
    assert elapsed < 1  # never entered the poll loop


def test_planned_state_returns_immediately_even_with_wait(agent_client):
    op = submit(agent_client)
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest="a" * 64)
    start = time.monotonic()
    response = agent_client.get(op["links"]["self"], params={"wait": 10})
    elapsed = time.monotonic() - start
    assert response.status_code == 200
    assert response.json()["state"] == "planned"
    assert response.json()["next_action"] == "execute_with_plan_digest"
    assert elapsed < 1


def test_state_change_mid_wait_returns_promptly(agent_client):
    op = submit(agent_client)

    def change_state_shortly():
        time.sleep(0.3)
        claimed = ledger.claim(op["id"], "plan")
        ledger.finish(claimed, "planned", plan_digest="b" * 64)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(change_state_shortly)
        start = time.monotonic()
        response = agent_client.get(op["links"]["self"], params={"wait": 10})
        elapsed = time.monotonic() - start
        future.result()

    assert response.status_code == 200
    assert response.json()["state"] == "planned"
    assert elapsed < 3  # well before the 10s deadline


def test_wait_elapses_with_unchanged_state(agent_client):
    op = submit(agent_client)
    start = time.monotonic()
    response = agent_client.get(op["links"]["self"], params={"wait": 1})
    elapsed = time.monotonic() - start
    assert response.status_code == 200
    assert response.json()["state"] == "queued"
    assert 1 <= elapsed < 2.5


def test_invisible_operation_is_404_without_waiting(agent_client):
    op = submit(agent_client, key="owned-by-someone-else")
    app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    try:
        start = time.monotonic()
        response = agent_client.get(f"/operations/{op['id']}", params={"wait": 10})
        elapsed = time.monotonic() - start
    finally:
        app.dependency_overrides.pop(caller_context, None)
    assert response.status_code == 404
    assert elapsed < 1


def test_wait_over_30_is_422(agent_client):
    op = submit(agent_client)
    response = agent_client.get(op["links"]["self"], params={"wait": 31})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_event_loop_is_not_blocked_while_waiting(agent_client):
    op = submit(agent_client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        waiter = pool.submit(agent_client.get, op["links"]["self"], params={"wait": 3})
        time.sleep(0.2)
        start = time.monotonic()
        quick = agent_client.get("/healthz")
        quick_elapsed = time.monotonic() - start
        assert quick.status_code == 200
        assert quick_elapsed < 1  # answered promptly despite the other request's open wait
        waited = waiter.result()
    assert waited.status_code == 200
    assert waited.json()["state"] == "queued"


def test_capability_is_advertised():
    assert TestClient(app).get("/v1/agent").json()["capabilities"]["operation_long_poll"] is True
