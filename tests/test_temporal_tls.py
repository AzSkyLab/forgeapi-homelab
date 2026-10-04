"""Opt-in Temporal TLS: one helper (app.dispatch.connect_temporal) builds the TLSConfig; every
call site uses it instead of calling Client.connect directly.

Exercised: the helper's TLS-off/CA-only/mTLS/server-name branches, the half-pair startup
refusal, and that dispatch.ready/dispatch, legacy's TemporalDispatcher and worker.main all pass
an explicit `tls=` kwarg to Client.connect (a direct, unpatched Client.connect call leaves `tls`
at its implicit None default, so an explicit kwarg proves the call went through the helper).
NOT exercised: a real TLS handshake against an actual Temporal server. temporalio's local test
server (temporalio.testing.WorkflowEnvironment, used elsewhere in this suite) does not offer a
TLS listener, so there is no real-server TLS test here.
"""

import asyncio
import inspect

import pytest
from temporalio.client import Client
from temporalio.service import TLSConfig

from app import legacy, worker
from app.dispatch import OperationDispatcher, connect_temporal
from app.settings import Settings, settings


class _FakeServiceClient:
    async def check_health(self):
        return None


class _FakeClient:
    def __init__(self):
        self.service_client = _FakeServiceClient()

    async def start_workflow(self, *args, **kwargs):
        return None


def _capture(monkeypatch):
    calls = []

    async def fake_connect(target_host, *, namespace="default", tls=None, **_kwargs):
        calls.append({"target_host": target_host, "namespace": namespace, "tls": tls})
        return _FakeClient()

    monkeypatch.setattr(Client, "connect", fake_connect)
    return calls


def test_tls_off_connects_with_tls_false(monkeypatch):
    calls = _capture(monkeypatch)
    asyncio.run(connect_temporal())
    assert calls[0]["tls"] is False


def test_ca_only(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"ca-bytes")
    monkeypatch.setattr(settings, "temporal_tls", True)
    monkeypatch.setattr(settings, "temporal_tls_ca_path", ca)
    asyncio.run(connect_temporal())
    tls = calls[0]["tls"]
    assert isinstance(tls, TLSConfig)
    assert tls.server_root_ca_cert == b"ca-bytes"
    assert tls.client_cert is None
    assert tls.client_private_key is None


def test_mtls_pair_is_read_as_bytes(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    cert, key = tmp_path / "client.pem", tmp_path / "client.key"
    cert.write_bytes(b"cert-bytes")
    key.write_bytes(b"key-bytes")
    monkeypatch.setattr(settings, "temporal_tls", True)
    monkeypatch.setattr(settings, "temporal_tls_cert_path", cert)
    monkeypatch.setattr(settings, "temporal_tls_key_path", key)
    asyncio.run(connect_temporal())
    tls = calls[0]["tls"]
    assert tls.client_cert == b"cert-bytes"
    assert tls.client_private_key == b"key-bytes"


def test_server_name_is_passed_as_domain(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(settings, "temporal_tls", True)
    monkeypatch.setattr(settings, "temporal_tls_server_name", "temporal.internal")
    asyncio.run(connect_temporal())
    assert calls[0]["tls"].domain == "temporal.internal"


def test_half_pair_cert_only_is_refused_at_startup(tmp_path):
    cert = tmp_path / "client.pem"
    cert.write_bytes(b"x")
    with pytest.raises(ValueError, match="temporal_tls_cert_path and temporal_tls_key_path"):
        Settings(temporal_tls_cert_path=cert, temporal_tls_key_path=None)


def test_half_pair_key_only_is_refused_at_startup(tmp_path):
    key = tmp_path / "client.key"
    key.write_bytes(b"x")
    with pytest.raises(ValueError, match="temporal_tls_cert_path and temporal_tls_key_path"):
        Settings(temporal_tls_cert_path=None, temporal_tls_key_path=key)


def test_full_pair_or_neither_is_accepted(tmp_path):
    Settings()  # neither: fine
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_bytes(b"c")
    key.write_bytes(b"k")
    Settings(temporal_tls_cert_path=cert, temporal_tls_key_path=key)  # both: fine


def test_dispatcher_ready_and_dispatch_use_the_helper(monkeypatch):
    calls = _capture(monkeypatch)
    asyncio.run(OperationDispatcher().ready())
    asyncio.run(OperationDispatcher().dispatch("op-1", "plan"))
    assert len(calls) == 2
    assert all(c["tls"] is False for c in calls)


def test_legacy_dispatcher_uses_the_helper(monkeypatch):
    calls = _capture(monkeypatch)
    dispatcher = legacy.TemporalDispatcher()
    asyncio.run(dispatcher("dep-1"))
    assert len(calls) == 1
    assert calls[0]["tls"] is False


def test_worker_main_calls_the_helper_not_client_connect_directly():
    """worker.main() starts recovery sweeps and an indefinite worker poll loop, so it cannot be
    run to completion in a unit test; this checks the call site at the source level instead."""
    source = inspect.getsource(worker.main)
    assert "connect_temporal()" in source
    assert "Client.connect" not in source
