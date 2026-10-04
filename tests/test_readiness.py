"""`/readyz` checks the ledger and Temporal without mutating anything; `/healthz` stays constant."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.dispatch import OperationDispatcher, get_dispatcher
from app.main import app
from app.settings import settings
from tests.operation_support import temporal_api


class _AlwaysReady:
    async def ready(self):
        return None


@pytest.fixture
def client():
    return TestClient(app)


def _init_ledger():
    """Simulate a ledger already set up (as it would be in a running deployment)."""
    with ledger.connect():
        pass


def test_healthz_unchanged(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "contract": "agent-v1"}


def test_readyz_ok_when_ledger_and_temporal_are_reachable(client):
    _init_ledger()
    app.dependency_overrides[get_dispatcher] = lambda: _AlwaysReady()
    try:
        response = client.get("/readyz")
    finally:
        app.dependency_overrides.pop(get_dispatcher, None)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ready", "checks": {"ledger": "ok", "temporal": "ok"}}


def test_readyz_names_only_ledger_when_data_dir_is_a_regular_file(client):
    settings.data_dir.write_text("not a directory")
    app.dependency_overrides[get_dispatcher] = lambda: _AlwaysReady()
    try:
        response = client.get("/readyz")
    finally:
        app.dependency_overrides.pop(get_dispatcher, None)
    assert response.status_code == 503, response.text
    assert response.json() == {
        "status": "not_ready",
        "checks": {"ledger": "unavailable", "temporal": "ok"},
    }


def test_readyz_names_only_temporal_when_unreachable(client, monkeypatch):
    _init_ledger()
    # A loopback port nothing listens on: a real connect attempt that fails fast, not a mock.
    monkeypatch.setattr(settings, "temporal_address", "127.0.0.1:1")
    app.dependency_overrides[get_dispatcher] = lambda: OperationDispatcher()
    try:
        response = client.get("/readyz")
    finally:
        app.dependency_overrides.pop(get_dispatcher, None)
    assert response.status_code == 503, response.text
    assert response.json() == {
        "status": "not_ready",
        "checks": {"ledger": "ok", "temporal": "unavailable"},
    }


def test_readyz_with_real_temporal_server():
    """Real coverage of the Temporal half, via the same local test-server fixture used by
    tests/test_temporal_operations.py."""

    async def scenario():
        async with temporal_api() as (api, _client):
            _init_ledger()
            response = await api.get("/readyz")
            assert response.status_code == 200, response.text
            assert response.json() == {
                "status": "ready",
                "checks": {"ledger": "ok", "temporal": "ok"},
            }

    asyncio.run(scenario())


def test_readyz_is_read_only(client):
    """No audit event, and no file beyond what an already-initialized ledger already has."""
    _init_ledger()
    before_files = sorted(p.name for p in settings.data_dir.iterdir())
    with ledger.connect(write=False) as con:
        before_counts = {
            table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("operations", "resources", "events")
        }
    app.dependency_overrides[get_dispatcher] = lambda: _AlwaysReady()
    try:
        response = client.get("/readyz")
    finally:
        app.dependency_overrides.pop(get_dispatcher, None)
    assert response.status_code == 200, response.text
    after_files = sorted(p.name for p in settings.data_dir.iterdir())
    assert after_files == before_files
    with ledger.connect(write=False) as con:
        after_counts = {
            table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("operations", "resources", "events")
        }
    assert after_counts == before_counts
