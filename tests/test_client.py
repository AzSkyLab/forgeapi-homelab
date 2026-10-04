"""The agent client only executes a reviewed plan through the HTTP boundary."""

import http.client
import io
import json
import socket
import threading
import time
import urllib.error
from pathlib import Path

import pytest
import uvicorn

from app.client import AmbiguousMutation, Client, ClientError, main
from app.contracts import Discovery
from app.main import app
from tests.test_operations import INTENT

OLD = Path(__file__).parent / "fixtures/v1"


@pytest.fixture
def http_client(recorded_dispatcher):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "local HTTP server did not start"
    try:
        yield Client(f"http://127.0.0.1:{port}/v1")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


def test_real_http_plan_review_execute_and_idempotent_retries(http_client, recorded_dispatcher):
    client = http_client
    assert client.discover()["supported_api_major"] == 1
    assert client.describe("demo")["name"] == "demo"
    assert client.validate(INTENT)["terraform_plan_performed"] is False
    submitted = client.submit(INTENT, "client-local-file")
    op_id = submitted["id"]
    assert submitted["state"] == "queued"
    assert not client.status(op_id)["terminal"]
    assert client.submit(INTENT, "client-local-file")["id"] == op_id
    assert list(recorded_dispatcher.pending) == [(op_id, "plan")]
    assert recorded_dispatcher.run_next()
    planned = client.status(op_id)
    assert planned["state"] == "planned"
    assert planned["changes"][0]["actions"] == ["create"]
    with pytest.raises(ClientError, match="digest differs"):
        client.execute(op_id, "0" * 64)
    assert client.execute(op_id, planned["plan_digest"])["state"] == "apply_queued"
    assert client.execute(op_id, planned["plan_digest"])["state"] == "apply_queued"
    assert list(recorded_dispatcher.pending) == [(op_id, "apply")]
    assert recorded_dispatcher.run_next()
    done = client.status(op_id)
    assert done["state"] == "succeeded"
    assert Path(done["outputs"]["path"]).read_text() == INTENT["inputs"]["content"]
    assert client.execute(op_id, planned["plan_digest"])["state"] == "succeeded"
    assert not recorded_dispatcher.pending
    assert [event["outcome"] for event in client.events(op_id)["items"]].count("accepted") == 2


@pytest.mark.parametrize("versioned", [False, True])
def test_real_http_operation_pages_remain_stable_after_insert(
    http_client, versioned
):
    client = http_client if versioned else Client(http_client.base_url.removesuffix("/v1"))
    original = [client.submit(INTENT, f"page-{i}")["id"] for i in range(4)]
    first = client.operations(limit=2)
    assert [item["id"] for item in first["items"]] == original[-1:-3:-1]
    assert first["next_before"] == original[-2]
    assert first["next_offset"] == 2
    assert all(
        item["links"]["self"].startswith("/v1/" if versioned else "/operations/")
        for item in first["items"]
    )
    newest = client.submit(INTENT, "page-newest")["id"]
    second = client.operations(limit=2, before=first["next_before"])
    seen = [item["id"] for item in first["items"] + second["items"]]
    assert seen == original[::-1]
    assert newest not in seen
    assert len(seen) == len(set(seen))
    assert second["next_before"] is None
    assert second["next_offset"] is None


@pytest.mark.parametrize("versioned", [False, True])
def test_real_http_event_pages_follow_insert_without_duplicates(
    http_client, recorded_dispatcher, versioned
):
    client = http_client if versioned else Client(http_client.base_url.removesuffix("/v1"))
    submitted = client.submit(INTENT, f"events-{versioned}")
    op_id = submitted["id"]
    assert recorded_dispatcher.run_next()
    first = client.events(op_id, limit=1)
    assert len(first["items"]) == 1
    assert first["next_after"] == first["items"][-1]["seq"]
    planned = client.status(op_id)
    client.execute(op_id, planned["plan_digest"])
    assert recorded_dispatcher.run_next()
    seen = first["items"][:]
    cursor = first["next_after"]
    while cursor is not None:
        page = client.events(op_id, after=cursor, limit=1)
        seen.extend(page["items"])
        cursor = page["next_after"]
    sequences = [event["seq"] for event in seen]
    assert len(sequences) >= 3
    assert sequences == sorted(set(sequences))
    assert all(event["operation_id"] == op_id for event in seen)
    assert client.events(op_id, after=sequences[-1])["items"] == []
    assert client.events(op_id, after=2**63 - 1) == {"items": [], "next_after": None}


def test_operations_accept_old_page_and_refuse_unsupported_cursor(monkeypatch):
    old = json.loads((OLD / "old_discovery.json").read_text())
    client = Client("http://localhost:8080")
    calls = []

    def response(method, link, *_args, **_kwargs):
        calls.append(link)
        return old if link.endswith("/agent") else {"items": [], "next_offset": None}

    monkeypatch.setattr(client, "_request", response)
    assert client.operations(offset=2) == {"items": [], "next_offset": None}
    assert calls[-1] == "/operations?limit=20&offset=2"
    cursor = "op_" + "a" * 32
    with pytest.raises(ClientError, match="stable operation pagination"):
        client.operations(before=cursor)
    assert len(calls) == 2
    client._discovery = Discovery.model_validate(
        {**old, "capabilities": {"stable_operation_pagination": False}}
    )
    with pytest.raises(ClientError, match="stable operation pagination"):
        client.operations(before=cursor)
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("body", "change"),
    [
        (INTENT, {"action": "destroy"}),
        (INTENT, {"pattern": "other"}),
        ({**INTENT, "resource_id": "res_" + "c" * 32}, {}),
    ],
)
def test_submit_rejects_contradictory_operation_without_followup(monkeypatch, body, change):
    discovery = json.loads((OLD / "old_discovery.json").read_text())
    operation = {**json.loads((OLD / "old_operation.json").read_text()), **change}
    client = Client("http://localhost:8080")
    calls = []

    def response(method, link, *_args, **_kwargs):
        calls.append((method, link))
        return discovery if link.endswith("/agent") else operation

    monkeypatch.setattr(client, "_request", response)
    with pytest.raises(AmbiguousMutation) as failure:
        client.submit(body, "same-key")
    assert failure.value.operation_id is None
    assert calls == [("GET", "http://localhost:8080/agent"), ("POST", "/operations")]


@pytest.mark.parametrize(
    ("body", "action"),
    [
        (INTENT, "deploy"),
        ({"pattern": "demo", "inputs": INTENT["inputs"]}, "deploy"),
        (json.dumps(INTENT), "deploy"),
        (json.dumps(INTENT, separators=(",", ":")).encode(), "deploy"),
        ({**INTENT, "resource_id": "res_" + "b" * 32}, "deploy"),
        ({**INTENT, "action": "destroy", "resource_id": "res_" + "b" * 32}, "destroy"),
    ],
)
def test_submit_accepts_matching_operation_and_preserves_body(monkeypatch, body, action):
    discovery = json.loads((OLD / "old_discovery.json").read_text())
    operation = {
        **json.loads((OLD / "old_operation.json").read_text()),
        "action": action,
        "future_field": "ignored-but-returned",
    }
    client = Client("http://localhost:8080")
    calls = []

    def response(method, link, *args, **_kwargs):
        calls.append((method, link, args[0] if args else None))
        return discovery if link.endswith("/agent") else operation

    monkeypatch.setattr(client, "_request", response)
    assert client.submit(body, "same-key") == operation
    assert [(method, link) for method, link, _ in calls] == [
        ("GET", "http://localhost:8080/agent"),
        ("POST", "/operations"),
    ]
    posted = calls[1][2]
    assert json.loads(posted) == (json.loads(body) if isinstance(body, (bytes, str)) else body)
    if isinstance(body, bytes):
        assert posted == body


@pytest.mark.parametrize("value", [1, "yes", "true", [], None])
def test_cursor_capability_requires_literal_json_boolean(monkeypatch, value):
    discovery = json.loads((OLD / "old_discovery.json").read_text())
    discovery["capabilities"] = {"stable_operation_pagination": value, "future_feature": True}
    client = Client("http://localhost:8080")
    calls = []

    def response(_method, link, *_args, **_kwargs):
        calls.append(link)
        return discovery if link.endswith("/agent") else {"items": [], "next_offset": None}

    monkeypatch.setattr(client, "_request", response)
    with pytest.raises(ClientError):
        client.operations(before="op_" + "a" * 32)
    assert calls == ["http://localhost:8080/agent"]


def test_literal_true_cursor_capability_keeps_unknown_boolean(monkeypatch):
    discovery = json.loads((OLD / "old_discovery.json").read_text())
    discovery["capabilities"] = {"stable_operation_pagination": True, "future_feature": True}
    client = Client("http://localhost:8080")
    calls = []
    page = {"items": [], "next_offset": None, "next_before": None}

    def response(_method, link, *_args, **_kwargs):
        calls.append(link)
        return discovery if link.endswith("/agent") else page

    monkeypatch.setattr(client, "_request", response)
    assert client.operations(before="op_" + "a" * 32) == page
    assert client._discovery.capabilities["future_feature"] is True
    assert len(calls) == 2


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"offset": -1},
    {"offset": True}, {"before": "bad"},
    {"before": "op_" + "a" * 32, "offset": 1},
])
def test_operations_reject_invalid_arguments_before_http(monkeypatch, kwargs):
    client = Client("http://localhost:8080")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: pytest.fail("HTTP request"))
    with pytest.raises(ClientError):
        client.operations(**kwargs)


@pytest.mark.parametrize("change", [
    {"state": "resumed"},
    {"links": {"self": "https://evil.example/operations"}},
])
def test_operations_reject_invalid_items(monkeypatch, change):
    old = json.loads((OLD / "old_discovery.json").read_text())
    operation = json.loads((OLD / "old_operation.json").read_text())
    operation.update(change)
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(old)
    monkeypatch.setattr(
        client, "_request", lambda *_args, **_kwargs: {
            "items": [operation], "next_offset": None
        }
    )
    with pytest.raises(ClientError):
        client.operations()


def _page_operation(char):
    operation = json.loads((OLD / "old_operation.json").read_text())
    operation["id"] = "op_" + char * 32
    operation["links"] = {
        name: link.replace("a" * 32, char * 32)
        for name, link in operation["links"].items()
    }
    return operation


@pytest.mark.parametrize(
    ("items", "next_offset", "next_before", "kwargs"),
    [
        (["a", "a"], None, None, {}),
        (["b"], None, None, {"before": "op_" + "b" * 32}),
        (["a", "b"], None, "op_" + "a" * 32, {}),
        ([], None, "op_" + "a" * 32, {}),
        (["a", "b"], 6, None, {"offset": 3}),
        (["a"], "1", None, {}),
        ([], 0, None, {}),
        (["a"], 1, None, {"before": "op_" + "c" * 32}),
    ],
)
def test_operations_reject_inconsistent_continuation(
    monkeypatch, items, next_offset, next_before, kwargs
):
    discovery = json.loads((OLD / "old_discovery.json").read_text())
    discovery["capabilities"] = {"stable_operation_pagination": True}
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(discovery)
    page = {
        "items": [_page_operation(char) for char in items],
        "next_offset": next_offset,
        "next_before": next_before,
    }
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: page)
    with pytest.raises(ClientError):
        client.operations(**kwargs)


@pytest.mark.parametrize(
    ("page", "kwargs"),
    [
        ({"items": [_page_operation("a")], "next_offset": 1}, {}),
        (
            {
                "items": [_page_operation("a"), _page_operation("b")],
                "next_offset": 2,
                "next_before": "op_" + "b" * 32,
                "future_field": "preserved",
            },
            {},
        ),
        (
            {
                "items": [_page_operation("a"), _page_operation("b")],
                "next_offset": None,
                "next_before": "op_" + "b" * 32,
            },
            {"before": "op_" + "c" * 32},
        ),
    ],
)
def test_operations_accept_consistent_old_and_stable_pages(monkeypatch, page, kwargs):
    discovery = json.loads((OLD / "old_discovery.json").read_text())
    discovery["capabilities"] = {"stable_operation_pagination": True}
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(discovery)
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: page)
    assert client.operations(**kwargs) == page


def test_cli_operations_print_one_requested_page(monkeypatch, capsys):
    page = {"items": [], "next_offset": None, "next_before": None, "extra": "preserved"}
    called = []

    def operations(self, *, limit, offset, before, resource_id=None, state=None):
        called.append((limit, offset, before, resource_id, state))
        return page

    monkeypatch.setattr(Client, "operations", operations)
    assert main([
        "--url", "http://localhost:8080", "operations", "--limit", "3",
        "--offset", "0", "--before", "op_" + "a" * 32,
    ]) == 0
    assert called == [(3, 0, "op_" + "a" * 32, None, None)]
    assert json.loads(capsys.readouterr().out) == page


@pytest.mark.parametrize("kwargs", [
    {"after": -1}, {"after": 2**63}, {"after": True}, {"after": 1.0},
    {"limit": 0}, {"limit": 101}, {"limit": False}, {"limit": 1.0},
])
def test_events_reject_invalid_arguments_before_http(monkeypatch, kwargs):
    client = Client("http://localhost:8080")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: pytest.fail("HTTP request"))
    with pytest.raises(ClientError):
        client.events("op_" + "a" * 32, **kwargs)


@pytest.mark.parametrize("change", [
    {"items": [{"seq": 1, "operation_id": "op_" + "b" * 32}]},
    {"items": [{"seq": 0}]},
    {"items": [{"seq": 2}, {"seq": 2}]},
    {"items": [{"seq": 3}, {"seq": 2}]},
    {"next_after": 99},
    {"items": [], "next_after": 1},
    {"items": [{"seq": True}]},
    {"next_after": True},
])
def test_events_reject_malformed_page(monkeypatch, change):
    old = json.loads((OLD / "old_discovery.json").read_text())
    operation = json.loads((OLD / "old_operation.json").read_text())
    op_id = operation["id"]
    event = {"seq": 1, "operation_id": op_id, "actor": "api", "action": "new.action",
             "outcome": "new-outcome", "timestamp": "2026-09-30T00:00:00Z"}
    page = {"items": [{**event, **item} for item in change.get("items", [{}])],
            "next_after": change.get("next_after")}
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(old)
    monkeypatch.setattr(client, "_request", lambda _method, link, **_kwargs: (
        operation if link == operation["links"]["self"] else page
    ))
    with pytest.raises(ClientError):
        client.events(op_id)


def test_events_old_discovery_additive_fields_and_cli_forwarding(monkeypatch, capsys):
    old = json.loads((OLD / "old_discovery.json").read_text())
    operation = json.loads((OLD / "old_operation.json").read_text())
    op_id = operation["id"]
    page = {"items": [{"seq": 4, "operation_id": op_id, "actor": "future",
                       "action": "future.action", "outcome": "future.outcome",
                       "timestamp": "2026-09-30T00:00:00Z", "new_field": 1}],
            "next_after": 4, "new_page_field": True}
    client = Client("http://localhost:8080")
    calls = []

    def response(_method, link, **_kwargs):
        calls.append(link)
        return old if link.endswith("/agent") else (
            operation if link == operation["links"]["self"] else page
        )

    monkeypatch.setattr(client, "_request", response)
    assert client.events(op_id, after=3, limit=7) == page
    assert calls[-1] == operation["links"]["events"] + "?after=3&limit=7"
    monkeypatch.setattr(Client, "events", lambda self, requested_id, *, after, limit: (
        calls.append((requested_id, after, limit)) or page
    ))
    assert main(["--url", "http://localhost:8080", "events", op_id,
                 "--after", "3", "--limit", "7"]) == 0
    assert calls[-1] == (op_id, 3, 7)
    assert json.loads(capsys.readouterr().out) == page


def test_old_discovery_and_false_capabilities_stop_optional_mutations(monkeypatch):
    old = json.loads((OLD / "old_discovery.json").read_text())
    operation = json.loads((OLD / "old_operation.json").read_text())
    older_server = Client("http://localhost:8080")
    monkeypatch.setattr(
        older_server, "_request",
        lambda method, *_args, **_kwargs: old if method == "GET" else operation,
    )
    assert older_server.submit(INTENT, "old-server-key")["id"] == operation["id"]
    client = Client("http://localhost:8080")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: old)
    assert client.discover()["contract"] == "agent-v1"
    assert client._bootstrap().capabilities == {}
    denied = {**old, "capabilities": {"idempotent_intents": False}}
    client._discovery = Discovery.model_validate(denied)
    with pytest.raises(ClientError, match="idempotent intents"):
        client.submit(INTENT, "key")
    client._discovery = Discovery.model_validate(
        {**old, "capabilities": {"operation_events": False}}
    )
    with pytest.raises(ClientError, match="operation events"):
        client.events("op_" + "a" * 32)

    client._discovery = Discovery.model_validate(
        {**old, "capabilities": {"exact_plan_execution": False}}
    )
    with pytest.raises(ClientError, match="exact plan execution"):
        client.execute(operation["id"], "a" * 64)


def test_uncertain_operation_stops_before_execute_post(monkeypatch):
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(
        json.loads((OLD / "old_discovery.json").read_text())
    )
    uncertain = json.loads((OLD / "old_operation.json").read_text())
    uncertain.update(state="uncertain", terminal=True,
                     next_action="reconcile_with_operator", plan_digest="a" * 64)
    calls = []

    def response(method, *_args, **_kwargs):
        calls.append(method)
        return uncertain

    monkeypatch.setattr(client, "_request", response)
    with pytest.raises(ClientError, match="not executable"):
        client.execute(uncertain["id"], "a" * 64)
    assert calls == ["GET"]


def test_cli_does_not_print_bearer_token(monkeypatch, capsys):
    token = "secret-token-value"
    monkeypatch.setenv("FORGEAPI_CLIENT_TOKEN", token + "\n")
    assert main(["--url", "http://localhost:8080", "discover"]) == 1
    captured = capsys.readouterr()
    assert token not in captured.err
    assert captured.out == ""


def test_unknown_or_incoherent_operation_never_drives_execution():
    client = Client("http://localhost:8080")
    old = json.loads((OLD / "old_operation.json").read_text())
    client._discovery = Discovery.model_validate(
        json.loads((OLD / "old_discovery.json").read_text())
    )
    for change in (
        {"state": "resumed"},
        {"state": "planned", "next_action": "poll"},
        {"id": "op_" + "c" * 32},
        {"links": {**old["links"], "execute": "https://evil.example/execute"}},
    ):
        with pytest.raises(ClientError):
            client._operation({**old, **change}, old["id"])
    with pytest.raises(ClientError, match="unsafe link"):
        client._url("https://evil.example/operations")
    with pytest.raises(ClientError, match="unsafe link"):
        client._url("//evil.example/operations")


def test_503_ambiguity_keeps_only_safe_identity_and_never_echoes_body(monkeypatch):
    client = Client("http://localhost:8080/v1")
    old = json.loads((OLD / "old_discovery.json").read_text())
    client._discovery = Discovery.model_validate(old)
    op_id = "op_" + "a" * 32
    body = {"error": {"code": "dispatch_unconfirmed", "operation_id": op_id,
                      "status_url": f"/operations/{op_id}"}}

    def failure(_request, timeout):
        raise urllib.error.HTTPError(
            "http://localhost:8080/v1/operations", 503, "secret diagnostic", {},
            io.BytesIO(json.dumps(body).encode()),
        )

    monkeypatch.setattr(client.opener, "open", failure)
    with pytest.raises(AmbiguousMutation) as raised:
        client._request("POST", "/v1/operations", b'{"content":"secret"}',
                        key="same-key", expected=202, mutation=True)
    assert raised.value.operation_id == op_id
    assert raised.value.status_url == f"http://localhost:8080/operations/{op_id}"
    assert "secret" not in str(raised.value)
    body["error"]["status_url"] = "https://evil.example/steal"
    with pytest.raises(AmbiguousMutation) as raised:
        client._request("POST", "/v1/operations", b"{}", key="same-key", expected=202,
                        mutation=True)
    assert raised.value.operation_id == op_id
    assert raised.value.status_url is None


def test_503_body_read_timeout_keeps_requested_operation_id(monkeypatch):
    client = Client("http://localhost:8080/v1")
    op_id = "op_" + "a" * 32

    class TimedOutBody:
        def read(self, *_args):
            raise TimeoutError("secret body read")

        def close(self):
            pass

    def failure(_request, timeout):
        raise urllib.error.HTTPError(
            "http://localhost:8080/v1/operations", 503, "secret diagnostic", {},
            TimedOutBody(),
        )

    monkeypatch.setattr(client.opener, "open", failure)
    with pytest.raises(AmbiguousMutation) as raised:
        client._request("POST", f"/v1/operations/{op_id}/execute", b"{}", mutation=True,
                        expected=202, known_operation_id=op_id)
    assert raised.value.operation_id == op_id
    assert "secret" not in str(raised.value)


def test_mutation_timeout_and_redirect_are_safe(monkeypatch):
    client = Client("http://localhost:8080/v1", timeout=0.1)

    def timeout(_request, timeout):
        raise TimeoutError("secret transport detail")

    monkeypatch.setattr(client.opener, "open", timeout)
    with pytest.raises(AmbiguousMutation, match="outcome unconfirmed") as raised:
        client._request("POST", "/v1/operations", b"{}", mutation=True, expected=202)
    assert raised.value.operation_id is None
    assert "secret" not in str(raised.value)

    def redirect(_request, timeout):
        raise urllib.error.HTTPError(
            "http://localhost:8080/v1/operations", 302, "secret redirect", {}, None
        )

    monkeypatch.setattr(client.opener, "open", redirect)
    with pytest.raises(ClientError, match="HTTP 302") as raised:
        client._request("GET", "/v1/agent")
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("failure", [500, 502, 504, http.client.IncompleteRead(b"secret")])
def test_truncated_or_server_failed_mutation_is_ambiguous(monkeypatch, failure):
    client = Client("http://localhost:8080/v1")

    def fail(_request, timeout):
        if isinstance(failure, int):
            raise urllib.error.HTTPError(
                "http://localhost:8080/v1/operations", failure, "secret server detail", {},
                io.BytesIO(b"bad response"),
            )
        raise failure

    monkeypatch.setattr(client.opener, "open", fail)
    with pytest.raises(AmbiguousMutation) as raised:
        client._request("POST", "/v1/operations", b"{}", mutation=True, expected=202)
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("field", ["id", "resource_id", "plan_digest"])
def test_contradictory_execute_response_is_ambiguous(monkeypatch, field):
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(
        json.loads((OLD / "old_discovery.json").read_text())
    )
    current = json.loads((OLD / "old_operation.json").read_text())
    current.update(state="planned", next_action="execute_with_plan_digest", plan_digest="a" * 64)
    changed = {"id": "op_" + "c" * 32, "resource_id": "res_" + "d" * 32,
               "plan_digest": "b" * 64}

    def response(method, *_args, **_kwargs):
        return current if method == "GET" else {**current, field: changed[field]}

    monkeypatch.setattr(client, "_request", response)
    with pytest.raises(AmbiguousMutation) as raised:
        client.execute(current["id"], current["plan_digest"])
    assert raised.value.operation_id == current["id"]


def test_real_http_patterns_and_resource_filters(http_client):
    patterns = http_client.patterns()
    assert patterns["items"] and all(item["links"]["self"] for item in patterns["items"])
    page = http_client.resources(pattern="no-such", environment="dev", state="ready")
    assert page["items"] == []
    assert http_client.resources(label="team=a")["items"] == []


def test_patterns_and_resource_filters_need_capabilities(monkeypatch):
    old = json.loads((OLD / "old_discovery.json").read_text())
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(old)
    monkeypatch.setattr(client, "_request", lambda *_a, **_k: pytest.fail("sent request"))
    with pytest.raises(ClientError, match="pattern listing"):
        client.patterns()
    client._discovery = Discovery.model_validate(
        {
            **old,
            "capabilities": {"resource_inventory": True},
            "links": {**old["links"], "resources": "/resources"},
        }
    )
    for kwargs in ({"pattern": "p"}, {"environment": "dev"}, {"state": "ready"}):
        with pytest.raises(ClientError, match="resource filters"):
            client.resources(**kwargs)
    with pytest.raises(ClientError, match="resource labels"):
        client.resources(label="team=a")


@pytest.mark.parametrize(
    "kwargs",
    [{"state": "bogus"}, {"pattern": ""}, {"environment": 1}, {"label": "novalue"}, {"label": 1}],
)
def test_resources_reject_invalid_filters_before_http(monkeypatch, kwargs):
    client = Client("http://localhost:8080")
    monkeypatch.setattr(client, "_bootstrap", lambda: pytest.fail("bootstrap"))
    with pytest.raises(ClientError, match="Invalid"):
        client.resources(**kwargs)


def test_patterns_reject_malformed_response(monkeypatch, http_client):
    http_client.discover()
    monkeypatch.setattr(http_client, "_request", lambda *_a, **_k: {"items": [{"name": 1}]})
    with pytest.raises(ClientError, match="Invalid server response"):
        http_client.patterns()


def test_cli_patterns_and_resource_filters_dispatch(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(Client, "patterns", lambda self: {"items": []})
    monkeypatch.setattr(
        Client, "resources", lambda self, **kwargs: called.append(kwargs) or {"items": []}
    )
    assert main(["--url", "http://localhost:8080", "patterns"]) == 0
    assert json.loads(capsys.readouterr().out) == {"items": []}
    assert main([
        "--url", "http://localhost:8080", "resources", "--pattern", "p",
        "--environment", "dev", "--state", "ready",
    ]) == 0
    assert called == [
        {"limit": 20, "after": None, "pattern": "p", "environment": "dev", "state": "ready",
         "label": None}
    ]
    assert main(["--url", "http://localhost:8080", "resources", "--label", "team=a"]) == 0
    assert called[-1]["label"] == "team=a"
    with pytest.raises(SystemExit):
        main(["--url", "http://localhost:8080", "resources", "--state", "bogus"])
