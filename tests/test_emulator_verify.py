"""Safety checks for the opt-in hosted object verifier."""

import http.client
import io
import json
import urllib.error

import pytest

from deploy.emulator import verify


def test_plan_checks_every_action():
    operation = {
        "changes": [
            {"type": "azurerm_resource_group", "actions": ["create"]},
            {"type": "azurerm_storage_account", "actions": ["create"]},
            {"type": "azurerm_storage_container", "actions": ["create"]},
        ],
        "plan_digest": "a" * 64,
    }
    assert verify.checked_plan(operation, "create", "azure") == "a" * 64
    operation["changes"][1]["actions"] = ["update"]
    with pytest.raises(RuntimeError, match="Unexpected"):
        verify.checked_plan(operation, "create", "azure")


def test_dispatch_retry_preserves_key_and_records_id(monkeypatch, tmp_path):
    calls = []
    response = io.BytesIO(
        json.dumps({"error": {"code": "dispatch_unconfirmed", "operation_id": "op_1"}}).encode()
    )

    def request(path, body=None, key=None):
        calls.append((path, body, key))
        if path == "/operations/op_1":
            return 200, {"id": "op_1", "resource_id": "res_1", "state": "planning"}
        if len([item for item in calls if item[0] == "/operations"]) == 1:
            raise urllib.error.HTTPError(path, 503, "", {}, response)
        return 202, {"id": "op_1", "resource_id": "res_1"}

    monkeypatch.setattr(verify, "request", request)
    monkeypatch.setattr(verify.time, "sleep", lambda _: None)
    path = tmp_path / "evidence.json"
    records = [{"create_key": "key-1"}]
    body = {"pattern": "floci-aws"}
    verify.api_mutation("/operations", body, "key-1", records[0], path, records)
    posts = [item for item in calls if item[0] == "/operations"]
    assert posts == [("/operations", body, "key-1")] * 2
    assert json.loads(path.read_text())[0]["operation_id"] == "op_1"
    assert records[0]["resource_id"] == "res_1"


def test_transport_retry_uses_identical_intent(monkeypatch, tmp_path):
    calls = []

    def request(path, body=None, key=None):
        calls.append((path, body, key))
        if len(calls) == 1:
            raise http.client.RemoteDisconnected("connection closed")
        return 202, {"id": "op_1", "resource_id": "res_1"}

    monkeypatch.setattr(verify, "request", request)
    monkeypatch.setattr(verify.time, "sleep", lambda _: None)
    records = [{"create_key": "key-1", "create_intent": {"environment": "dev"}}]
    verify.api_mutation(
        "/operations",
        records[0]["create_intent"],
        "key-1",
        records[0],
        tmp_path / "evidence.json",
        records,
    )
    assert calls == [("/operations", {"environment": "dev"}, "key-1")] * 2


def test_replay_resource_mismatch_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(
        verify, "request", lambda *args: (202, {"id": "op_1", "resource_id": "res_other"})
    )
    records = [{"operation_id": "op_1", "resource_id": "res_owned"}]
    with pytest.raises(RuntimeError, match="Resource identity changed"):
        verify.api_mutation(
            "/execute",
            {"plan_digest": "a" * 64},
            None,
            records[0],
            tmp_path / "evidence.json",
            records,
        )
    assert records[0]["resource_id"] == "res_owned"


def test_uncertain_dispatch_stops_without_retry(monkeypatch, tmp_path):
    calls = []
    response = io.BytesIO(
        json.dumps({"error": {"code": "dispatch_unconfirmed", "operation_id": "op_1"}}).encode()
    )

    def request(path, body=None, key=None):
        calls.append(path)
        if path == "/operations":
            raise urllib.error.HTTPError(path, 503, "", {}, response)
        return 200, {"state": "uncertain", "resource_id": "res_1"}

    monkeypatch.setattr(verify, "request", request)
    records = [{"create_key": "key-1"}]
    with pytest.raises(RuntimeError, match="uncertain"):
        verify.api_mutation(
            "/operations",
            {"pattern": "floci-aws"},
            "key-1",
            records[0],
            tmp_path / "evidence.json",
            records,
        )
    assert calls == ["/operations", "/operations/op_1"]
    assert records[0]["operation_id"] == "op_1"
    assert json.loads((tmp_path / "evidence.json").read_text())[0]["resource_id"] == "res_1"


def test_poll_obeys_interval_and_deadline(monkeypatch):
    calls = iter(
        [
            {"state": "planning", "terminal": False, "poll_after_seconds": 3},
            {"state": "planned", "terminal": False},
        ]
    )
    sleeps = []
    monkeypatch.setattr(verify, "request", lambda *_: (200, next(calls)))
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0)
    monkeypatch.setattr(verify.time, "sleep", sleeps.append)
    assert verify.poll({"id": "op_1"}, "planned")["state"] == "planned"
    assert sleeps == [3]
    clock = iter([0, 601])
    monkeypatch.setattr(verify.time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError):
        verify.poll({"id": "op_1"}, "planned")


@pytest.mark.parametrize("provider", ["azure", "aws", "gcp"])
@pytest.mark.parametrize("failure", [None, "hash", "delete", "absence"])
def test_object_flow_and_mismatch_stop(monkeypatch, tmp_path, provider, failure):
    name = None
    mutations = []
    seen = []
    object_present = False
    object_bytes = b""

    def request(path, body=None, key=None):
        nonlocal name
        if path == "/intents/validate":
            name = body["inputs"]["name"]
            return 200, {"commit": "pinned"}
        raise AssertionError(path)

    def mutate(path, body, key, record, evidence_path, records, id_field="operation_id"):
        mutations.append((path, body, key, id_field))
        operation_id = "op_destroy" if id_field == "destroy_operation_id" else "op_create"
        record[id_field] = operation_id
        record["resource_id"] = "res_owned"
        verify.save_evidence(evidence_path, records)
        return {
            "id": operation_id,
            "resource_id": "res_owned",
            "links": {"execute": f"/{operation_id}/execute"},
        }

    def poll(operation, state):
        if state == "planned":
            types = {
                "azure": [
                    "azurerm_resource_group",
                    "azurerm_storage_account",
                    "azurerm_storage_container",
                ],
                "aws": ["aws_s3_bucket"],
                "gcp": ["google_storage_bucket"],
            }[provider]
            action = "delete" if operation["id"] == "op_destroy" else "create"
            return {
                "changes": [{"type": typ, "actions": [action]} for typ in types],
                "plan_digest": "a" * 64,
            }
        return {
            "resource_id": "res_owned",
            "commit": "pinned",
            "outputs": {"name": name, "container_name": "data"},
        }

    def cloud(url, method="GET", data=None, headers=None):
        nonlocal object_present, object_bytes
        seen.append((url, method, headers))
        if method in ("PUT", "POST"):
            object_present, object_bytes = True, data
            return 201, b""
        if method == "DELETE":
            if failure == "delete":
                return 500, b""
            object_present = False
            return 204, b""
        if object_present:
            return 200, object_bytes[:-1] if failure == "hash" else object_bytes
        if failure == "absence" and any(item[1] == "DELETE" for item in seen):
            return 200, b"stale"
        return 404, b""

    infra_checks = iter([404, 200, 200, 404, 404] if provider == "azure" else [404, 200, 404])
    monkeypatch.setattr(verify, "request", request)
    monkeypatch.setattr(verify, "api_mutation", mutate)
    monkeypatch.setattr(verify, "poll", poll)
    monkeypatch.setattr(verify, "cloud_bytes", cloud)
    monkeypatch.setattr(verify, "readback", lambda _: next(infra_checks))
    path = tmp_path / "evidence.json"
    records = []
    if failure:
        with pytest.raises(RuntimeError):
            verify.consume(provider, "dev", path, records)
        assert not any(item[3] == "destroy_operation_id" for item in mutations)
        saved = json.loads(path.read_text())[0]
        assert saved["stage"] in ("uploaded", "bytes_verified", "object_deleted")
        if failure == "hash":
            assert saved["read_length"] == saved["length"] - 1
            assert saved["read_sha256"] != saved["sha256"]
    else:
        verify.consume(provider, "dev", path, records)
        assert records[0]["stage"] == "complete"
        assert records[0]["read_sha256"] == records[0]["sha256"]
        assert records[0]["read_length"] == records[0]["length"]
        assert records[0]["create_intent"]["expected_commit"] == "pinned"
        assert records[0]["destroy_intent"]["resource_id"] == "res_owned"
        assert records[0]["storage_before_status"] == 404
        assert records[0]["storage_present_status"] == 200
        assert any(item[3] == "destroy_operation_id" for item in mutations)
        assert seen[-1][1] == "GET"
        if provider == "gcp":
            assert any("uploadType=media" in item[0] and item[1] == "POST" for item in seen)
        if provider == "azure":
            assert any(item[2] and item[2].get("x-ms-blob-type") == "BlockBlob" for item in seen)
