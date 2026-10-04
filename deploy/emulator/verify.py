"""Call the remote API through its SSH tunnel; independently verify emulator resources.

python deploy/emulator/verify.py [--keep] [--evidence PATH]
Uses only stdlib; the caller desktop does not run Terraform or a worker.
"""

import argparse
import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API = "http://127.0.0.1:28000"
CLOUD_PORT = 24500


def request(path, body=None, key=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(
        API + path, json.dumps(body).encode() if body is not None else None, headers
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status, json.load(response)


def readback(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def wait(operation, expected):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        _, current = request(operation["links"]["self"])
        if current["state"] == expected:
            return current
        if current["terminal"]:
            raise RuntimeError(f"Unexpected outcome: {current['id']} {current['state']}")
        time.sleep(0.5)
    raise TimeoutError(f"Waiting for {operation['id']} to reach {expected}")


def lifecycle(provider, keep, environment=None):
    name = f"forge{provider}{uuid.uuid4().hex[:12]}"
    target = {
        "aws": f"http://127.0.0.1:{CLOUD_PORT + 66}/{name}",
        "azure": f"http://127.0.0.1:{CLOUD_PORT + 77}/subscriptions/"
        f"00000000-0000-0000-0000-000000000001/resourcegroups/{name}?api-version=2024-03-01",
        "gcp": f"http://127.0.0.1:{CLOUD_PORT + 88}/storage/v1/b/{name}",
    }[provider]
    if environment and provider == "azure":
        target = target.split("?")[0] + (
            f"/providers/Microsoft.Storage/storageAccounts/{name}?api-version=2023-05-01"
        )
    intent = {"pattern": f"floci-{provider}", "version": "v1.0.0", "inputs": {"name": name}}
    if environment:
        intent["environment"] = environment
        _, described = request(f"/patterns/floci-{provider}?environment={environment}")
        assert described["cloud"] == provider
        assert set(described["input_schema"]["properties"]) == {"name"}
        # Target identifiers and region must never become caller-controlled inputs.
        for field in ("subscription_id", "aws_account_id", "project_id", "region"):
            try:
                request(
                    "/operations",
                    {**intent, "inputs": {"name": name, field: "override"}},
                    f"refused-{field}-{name}",
                )
            except urllib.error.HTTPError as error:
                assert error.code == 422
            else:
                raise AssertionError(f"Platform input accepted: {field}")
    _, checked = request("/intents/validate", intent)
    intent["expected_commit"] = checked["commit"]
    status, operation = request("/operations", intent, name)
    assert status == 202
    assert request("/operations", intent, name)[1]["id"] == operation["id"]
    planned = wait(operation, "planned")
    assert planned["changes"][0]["actions"] == ["create"]
    assert readback(target) == 404
    blob = f"http://127.0.0.1:{CLOUD_PORT + 77}/{name}/data?restype=container"
    if environment and provider == "azure":
        assert readback(blob) == 404
    execution = {"plan_digest": planned["plan_digest"]}
    assert request(operation["links"]["execute"], execution)[0] == 202
    assert request(operation["links"]["execute"], execution)[0] == 202
    done = wait(operation, "succeeded")
    assert done["outputs"]["name"] == name
    if environment:
        assert done["withheld_outputs"] == ["placement_id"]
        assert "placement_id" not in done["outputs"]
    assert readback(target) == 200
    if environment and provider == "azure":
        assert readback(blob) == 200
    events = request(operation["links"]["events"])[1]
    assert [event["outcome"] for event in events["items"]] == [
        "accepted",
        "planning",
        "planned",
        "accepted",
        "applying",
        "succeeded",
    ]
    evidence = {
        "provider": provider,
        "name": name,
        "operation": done,
        "events": events,
        "readback": [404, 200],
    }
    if not keep:
        status, destroy = request(
            "/operations",
            {"pattern": intent["pattern"], "action": "destroy", "resource_id": done["resource_id"]},
            f"destroy-{name}",
        )
        assert status == 202
        planned = wait(destroy, "planned")
        assert planned["changes"][0]["actions"] == ["delete"]
        assert (
            request(destroy["links"]["execute"], {"plan_digest": planned["plan_digest"]})[0] == 202
        )
        evidence["destroy"] = wait(destroy, "succeeded")
        assert readback(target) == 404
        if environment and provider == "azure":
            assert readback(blob) == 404
        evidence["readback"].append(404)
    print(f"{provider}: {operation['id']} succeeded; readback {evidence['readback']}", flush=True)
    return evidence


def save_evidence(path, records):
    """Replace only this run's file; never leave a partly written JSON record."""
    for record in records:
        record["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as output:
            json.dump(records, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def api_mutation(path, body, key, record, evidence_path, records, id_field="operation_id"):
    """Retry an ambiguous dispatch with the identical body and key only."""
    for attempt in range(3):
        try:
            status, result = request(path, body, key)
            assert status == 202
            if record.get(id_field) and record[id_field] != result["id"]:
                raise RuntimeError("Operation identity changed")
            record[id_field] = result["id"]
            if record.get("resource_id") and result["resource_id"] != record["resource_id"]:
                raise RuntimeError("Resource identity changed")
            record["resource_id"] = result["resource_id"]
            save_evidence(evidence_path, records)
            return result
        except urllib.error.HTTPError as error:
            if error.code != 503:
                raise RuntimeError(f"API refused request with HTTP {error.code}") from None
            try:
                detail = json.load(error)["error"]
            except (ValueError, KeyError):
                raise RuntimeError(
                    "Unrecognized API 503; inspect before further mutation"
                ) from None
            if detail.get("code") != "dispatch_unconfirmed":
                raise RuntimeError("Unrecognized dispatch response") from None
            if operation_id := detail.get("operation_id"):
                if record.get(id_field) and record[id_field] != operation_id:
                    raise RuntimeError("Operation identity changed") from None
                record[id_field] = operation_id
                save_evidence(evidence_path, records)
                _, current = request(f"/operations/{operation_id}")
                if record.get("resource_id") and current["resource_id"] != record["resource_id"]:
                    raise RuntimeError("Resource identity changed") from None
                record["resource_id"] = current["resource_id"]
                save_evidence(evidence_path, records)
                if current["state"] == "uncertain":
                    raise RuntimeError("Operation is uncertain; reconcile manually") from None
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionResetError,
            http.client.RemoteDisconnected,
            json.JSONDecodeError,
        ):
            if record.get(id_field):
                _, current = request(f"/operations/{record[id_field]}")
                if record.get("resource_id") and current["resource_id"] != record["resource_id"]:
                    raise RuntimeError("Resource identity changed") from None
                record["resource_id"] = current["resource_id"]
                save_evidence(evidence_path, records)
                if current["state"] == "uncertain":
                    raise RuntimeError("Operation is uncertain; reconcile manually") from None
        if attempt < 2:
            time.sleep(2)
    raise RuntimeError("Dispatch remains unconfirmed; use saved key and operation ID to reconcile")


def poll(operation, state):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        _, current = request(f"/operations/{operation['id']}")
        if current["state"] == state:
            return current
        if current["terminal"]:
            raise RuntimeError(f"Operation {current['id']} ended {current['state']}")
        time.sleep(max(0.1, min(current.get("poll_after_seconds") or 2, 10)))
    raise TimeoutError(f"Operation {operation['id']} did not reach {state}")


def checked_plan(operation, action, provider):
    changes = operation["changes"]
    expected = {
        "azure": {"azurerm_resource_group", "azurerm_storage_account", "azurerm_storage_container"},
        "aws": {"aws_s3_bucket"},
        "gcp": {"google_storage_bucket"},
    }[provider]
    if (
        not changes
        or {change["type"] for change in changes} != expected
        or len(changes) != len(expected)
        or any(change["actions"] != [action] for change in changes)
        or not operation.get("plan_digest")
    ):
        raise RuntimeError(f"Unexpected {provider} {action} plan; no execution")
    return operation["plan_digest"]


def cloud_bytes(url, method="GET", data=None, headers=None):
    try:
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def consume(provider, environment, evidence_path, records):
    name = f"forge{provider}{uuid.uuid4().hex[:12]}"
    object_name = f"proof-{uuid.uuid4().hex}.bin"
    payload = b"ForgeAPI object consumption\n" + bytes(range(256)) * 4 + uuid.uuid4().bytes
    digest = hashlib.sha256(payload).hexdigest()
    record = {
        "provider": provider,
        "name": name,
        "object": object_name,
        "length": len(payload),
        "sha256": digest,
        "stage": "prepared",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    records.append(record)
    save_evidence(evidence_path, records)
    target = {
        "aws": f"http://127.0.0.1:{CLOUD_PORT + 66}/{name}",
        "azure": f"http://127.0.0.1:{CLOUD_PORT + 77}/subscriptions/"
        f"00000000-0000-0000-0000-000000000001/resourcegroups/{name}"
        f"/providers/Microsoft.Storage/storageAccounts/{name}?api-version=2023-05-01",
        "gcp": f"http://127.0.0.1:{CLOUD_PORT + 88}/storage/v1/b/{name}",
    }[provider]
    intent = {
        "pattern": f"floci-{provider}",
        "version": "v1.0.0",
        "environment": environment,
        "inputs": {"name": name},
    }
    _, checked = request("/intents/validate", intent)
    if not checked.get("commit"):
        raise RuntimeError("Pattern version was not pinned")
    intent["expected_commit"] = checked["commit"]
    record["commit"] = checked["commit"]
    record["create_key"] = f"consume-create-{name}"
    record["create_intent"] = intent.copy()
    record["stage"] = "create_submitting"
    save_evidence(evidence_path, records)
    operation = api_mutation(
        "/operations", intent, record["create_key"], record, evidence_path, records
    )
    record["create_http_status"] = 202
    record["stage"] = "create_accepted"
    save_evidence(evidence_path, records)
    planned = poll(operation, "planned")
    plan_digest = checked_plan(planned, "create", provider)
    record["create_plan_digest"] = plan_digest
    record["stage"] = "create_planned"
    save_evidence(evidence_path, records)
    record["storage_before_status"] = readback(target)
    save_evidence(evidence_path, records)
    if record["storage_before_status"] != 404:
        raise RuntimeError("Storage was present before execute")
    record["stage"] = "create_executing"
    save_evidence(evidence_path, records)
    api_mutation(
        operation["links"]["execute"],
        {"plan_digest": plan_digest},
        None,
        record,
        evidence_path,
        records,
    )
    done = poll(operation, "succeeded")
    if done["resource_id"] != record["resource_id"] or done["commit"] != checked["commit"]:
        raise RuntimeError("Created resource identity changed")
    outputs = done["outputs"]
    if outputs["name"] != name:
        raise RuntimeError("Storage output does not match owned name")
    container = outputs.get("container_name") if provider == "azure" else None
    if provider == "azure" and container != "data":
        raise RuntimeError("Azure container output does not match fixture")
    record["storage_present_status"] = readback(target)
    save_evidence(evidence_path, records)
    if record["storage_present_status"] != 200:
        raise RuntimeError("Created storage absent")
    if provider == "azure":
        record["container_present_status"] = readback(
            f"http://127.0.0.1:{CLOUD_PORT + 77}/{name}/{container}?restype=container"
        )
        save_evidence(evidence_path, records)
        if record["container_present_status"] != 200:
            raise RuntimeError("Created Azure container absent")
    record.update(stage="storage_present", container=container)
    save_evidence(evidence_path, records)
    object_url = {
        "azure": f"http://127.0.0.1:{CLOUD_PORT + 77}/{name}/{container}/{object_name}",
        "aws": f"http://127.0.0.1:{CLOUD_PORT + 66}/{name}/{object_name}",
        "gcp": f"http://127.0.0.1:{CLOUD_PORT + 88}/storage/v1/b/{name}/o/{object_name}?alt=media",
    }[provider]
    record["object_before_status"] = cloud_bytes(object_url)[0]
    save_evidence(evidence_path, records)
    if record["object_before_status"] != 404:
        raise RuntimeError("Object name was present before upload")
    record["stage"] = "object_absent_before_upload"
    save_evidence(evidence_path, records)
    upload_url = (
        f"http://127.0.0.1:{CLOUD_PORT + 88}/upload/storage/v1/b/{name}/o?"
        + urllib.parse.urlencode({"uploadType": "media", "name": object_name})
        if provider == "gcp"
        else object_url
    )
    headers = {"Content-Type": "application/octet-stream"}
    if provider == "azure":
        headers["x-ms-blob-type"] = "BlockBlob"
    upload_status, _ = cloud_bytes(
        upload_url, "POST" if provider == "gcp" else "PUT", payload, headers
    )
    record["upload_status"] = upload_status
    save_evidence(evidence_path, records)
    if upload_status not in (200, 201):
        raise RuntimeError(f"Object upload returned HTTP {upload_status}")
    record.update(stage="uploaded", upload_status=upload_status)
    save_evidence(evidence_path, records)
    read_status, downloaded = cloud_bytes(object_url)
    read_digest = hashlib.sha256(downloaded).hexdigest()
    record.update(read_status=read_status, read_sha256=read_digest, read_length=len(downloaded))
    save_evidence(evidence_path, records)
    if (
        read_status != 200
        or len(downloaded) != len(payload)
        or read_digest != digest
        or downloaded != payload
    ):
        raise RuntimeError("Object byte readback failed")
    record.update(
        stage="bytes_verified",
        read_status=read_status,
        read_sha256=read_digest,
        read_length=len(downloaded),
    )
    save_evidence(evidence_path, records)
    delete_status, _ = cloud_bytes(object_url.split("?alt=media")[0], "DELETE")
    record["delete_status"] = delete_status
    save_evidence(evidence_path, records)
    if delete_status not in (202, 204):
        raise RuntimeError(f"Object delete returned HTTP {delete_status}")
    record.update(stage="object_deleted", delete_status=delete_status)
    save_evidence(evidence_path, records)
    absent_status, _ = cloud_bytes(object_url)
    record["absent_status"] = absent_status
    save_evidence(evidence_path, records)
    if absent_status != 404:
        raise RuntimeError(f"Deleted object read returned HTTP {absent_status}")
    record.update(
        stage="object_absent", absent_status=absent_status, destroy_key=f"consume-destroy-{name}"
    )
    destroy_intent = {
        "pattern": intent["pattern"],
        "action": "destroy",
        "resource_id": done["resource_id"],
        "environment": environment,
    }
    record["destroy_intent"] = destroy_intent.copy()
    save_evidence(evidence_path, records)
    destroy = api_mutation(
        "/operations",
        destroy_intent,
        record["destroy_key"],
        record,
        evidence_path,
        records,
        id_field="destroy_operation_id",
    )
    record["destroy_http_status"] = 202
    record["stage"] = "destroy_accepted"
    save_evidence(evidence_path, records)
    destroy_plan = poll(destroy, "planned")
    destroy_digest = checked_plan(destroy_plan, "delete", provider)
    record.update(stage="destroy_planned", destroy_plan_digest=destroy_digest)
    save_evidence(evidence_path, records)
    api_mutation(
        destroy["links"]["execute"],
        {"plan_digest": destroy_digest},
        None,
        record,
        evidence_path,
        records,
        id_field="destroy_operation_id",
    )
    poll(destroy, "succeeded")
    record["storage_absent_status"] = readback(target)
    save_evidence(evidence_path, records)
    if record["storage_absent_status"] != 404:
        raise RuntimeError("Storage remained after destroy")
    if provider == "azure":
        record["container_absent_status"] = readback(
            f"http://127.0.0.1:{CLOUD_PORT + 77}/{name}/{container}?restype=container"
        )
        save_evidence(evidence_path, records)
        if record["container_absent_status"] != 404:
            raise RuntimeError("Azure container remained after destroy")
    record["stage"] = "complete"
    save_evidence(evidence_path, records)
    print(f"{provider}: {operation['id']} object bytes verified and resource destroyed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Keep newly created emulator resources")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--api", default=API)
    parser.add_argument("--cloud-port-base", type=int, default=CLOUD_PORT)
    parser.add_argument("--environment")
    parser.add_argument("--consume-objects", action="store_true")
    args = parser.parse_args()
    if args.consume_objects:
        if args.keep or not args.environment:
            parser.error("--consume-objects requires --environment and rejects --keep")
        for port in (
            args.cloud_port_base + 66,
            args.cloud_port_base + 77,
            args.cloud_port_base + 88,
        ):
            if not 1 <= port <= 65535:
                parser.error("invalid cloud port")
        parsed = urllib.parse.urlsplit(args.api)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 38000
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            parser.error("object consumption requires the placement API at 127.0.0.1:38000")
        if args.cloud_port_base != 34500:
            parser.error("object consumption requires placement emulator ports 34566/34577/34588")
        if args.evidence.exists():
            parser.error("object consumption evidence path already exists")
    API, CLOUD_PORT = args.api, args.cloud_port_base
    assert request("/healthz")[1]["contract"] == "agent-v1"
    discovery = request("/agent")[1]
    results = []
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    if args.consume_objects:
        with args.evidence.open("x") as output:
            output.write("[]\n")
    for provider in ("azure", "aws", "gcp"):
        assert f"floci-{provider}" in discovery["patterns"]
        if args.consume_objects:
            try:
                consume(provider, args.environment, args.evidence, results)
            except Exception as error:
                if results:
                    results[-1]["stopped_at"] = results[-1]["stage"]
                    results[-1]["stage"] = "stopped"
                    results[-1]["failure_type"] = type(error).__name__
                    results[-1]["next_action"] = (
                        "inspect saved IDs, operation state, and emulator state manually"
                    )
                    save_evidence(args.evidence, results)
                raise
        else:
            results.append(lifecycle(provider, args.keep, args.environment))
            args.evidence.write_text(json.dumps(results, indent=2) + "\n")
