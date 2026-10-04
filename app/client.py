"""Explicit HTTP client for the agent-v1 control plane."""

import argparse
import http.client
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import get_args

from pydantic import ValidationError

from app.contracts import (
    Discovery,
    EventPage,
    Execute,
    Intent,
    Operation,
    OperationPage,
    PatternDescription,
    PatternList,
    Reconcile,
    Resource,
    ResourcePage,
    ValidationResult,
)

TOKEN_ENV = "FORGEAPI_CLIENT_TOKEN"
OPERATION_ID = re.compile(r"op_[0-9a-f]{32}\Z")
RESOURCE_ID = re.compile(r"res_[0-9a-f]{32}\Z")
KEY = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
OPERATION_STATES = frozenset(get_args(Operation.model_fields["state"].annotation))
RESOURCE_STATES = frozenset(get_args(Resource.model_fields["state"].annotation))


class ClientError(Exception):
    """A safe diagnostic; never includes raw server detail or submitted body."""


class AmbiguousMutation(ClientError):
    def __init__(self, operation_id=None, status_url=None):
        self.operation_id = operation_id
        self.status_url = status_url
        hint = f" operation_id={operation_id} status_url={status_url}" if operation_id else ""
        super().__init__(
            f"Mutation outcome unconfirmed; retain and retry the identical request.{hint}"
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _json_body(body):
    try:
        data = json.loads(body) if isinstance(body, (bytes, str)) else body
        Intent.model_validate(data)
        return body if isinstance(body, bytes) else json.dumps(data).encode()
    except (ValueError, TypeError, ValidationError):
        raise ClientError("Invalid intent body") from None


class Client:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 10):
        try:
            parsed = urllib.parse.urlsplit(base_url)
            port = parsed.port
        except (TypeError, ValueError):
            raise ClientError("Invalid API URL") from None
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ClientError("Invalid API URL")
        try:
            loopback = (
                parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            loopback = False
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ClientError("API URL must use HTTPS or loopback HTTP")
        if not parsed.hostname or timeout <= 0:
            raise ClientError("Invalid API URL or timeout")
        if token is not None and (
            not token
            or not token.isascii()
            or any(not 33 <= ord(character) <= 126 for character in token)
        ):
            raise ClientError("Invalid bearer token")
        self.base_url = base_url.rstrip("/")
        self.origin = (parsed.scheme, parsed.hostname, port)
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self._discovery = None
        self._raw_discovery = None

    def _url(self, link: str) -> str:
        try:
            url = urllib.parse.urljoin(self.base_url + "/", link)
            parsed = urllib.parse.urlsplit(url)
            same_origin = (parsed.scheme, parsed.hostname, parsed.port) == self.origin
        except (TypeError, ValueError):
            raise ClientError("Server advertised an unsafe link") from None
        if not same_origin or parsed.username or parsed.password or parsed.fragment:
            raise ClientError("Server advertised an unsafe link")
        return url

    def _request(
        self,
        method: str,
        link: str,
        body=None,
        key=None,
        expected=200,
        mutation=False,
        known_operation_id=None,
        timeout=None,
    ):
        url = self._url(link)
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
        except ValueError:
            raise ClientError("Invalid request") from None
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                if response.status != expected:
                    if mutation:
                        raise AmbiguousMutation(known_operation_id)
                    raise ClientError(f"Unexpected HTTP {response.status}")
                try:
                    result = json.load(response)
                except (ValueError, UnicodeDecodeError, http.client.HTTPException):
                    if mutation:
                        raise AmbiguousMutation(known_operation_id) from None
                    raise ClientError("Invalid JSON response") from None
                if not isinstance(result, dict):
                    if mutation:
                        raise AmbiguousMutation(known_operation_id)
                    raise ClientError("Invalid JSON response")
                return result
        except urllib.error.HTTPError as error:
            if mutation and error.code == 503:
                try:
                    envelope = json.load(error)
                    detail = envelope.get("error") if isinstance(envelope, dict) else None
                    if not isinstance(detail, dict):
                        raise AmbiguousMutation(known_operation_id)
                    if detail.get("code") == "dispatch_unconfirmed":
                        operation_id = known_operation_id or detail.get("operation_id")
                        status_url = detail.get("status_url")
                        if not isinstance(operation_id, str) or not OPERATION_ID.fullmatch(
                            operation_id
                        ):
                            operation_id = None
                        if not isinstance(status_url, str):
                            status_url = None
                        elif status_url:
                            try:
                                candidate = self._url(status_url)
                                path = urllib.parse.urlsplit(candidate)
                                expected_path = (
                                    (
                                        self._discovery.links.operations.rstrip("/")
                                        + "/"
                                        + operation_id
                                    )
                                    if operation_id and self._discovery
                                    else None
                                )
                                status_url = (
                                    candidate
                                    if (
                                        expected_path
                                        and candidate == self._url(expected_path)
                                        and not path.query
                                    )
                                    else None
                                )
                            except ClientError:
                                status_url = None
                        raise AmbiguousMutation(operation_id, status_url)
                    if detail.get("code") != "service_unavailable":
                        raise AmbiguousMutation(known_operation_id)
                except (ValueError, TypeError, OSError, http.client.HTTPException):
                    raise AmbiguousMutation(known_operation_id) from None
            if mutation and error.code in (500, 502, 504):
                raise AmbiguousMutation(known_operation_id) from None
            raise ClientError(f"HTTP {error.code}") from None
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            http.client.HTTPException,
        ):
            if mutation:
                raise AmbiguousMutation(known_operation_id) from None
            raise ClientError("Transport failure") from None

    def _bootstrap(self) -> Discovery:
        if self._discovery is None:
            raw = self._request("GET", self.base_url + "/agent")
            try:
                discovered = Discovery.model_validate(raw)
            except ValidationError:
                raise ClientError("Unsupported agent discovery contract") from None
            for link in discovered.links.model_dump(by_alias=True).values():
                if link:
                    self._url(link.replace("{name}", "probe"))
            self._discovery = discovered
            self._raw_discovery = raw
        return self._discovery

    def discover(self):
        self._bootstrap()
        return self._raw_discovery

    def describe(self, pattern, version=None, business_unit=None, environment=None):
        link = self._bootstrap().links.patterns.replace(
            "{name}", urllib.parse.quote(pattern, safe="")
        )
        query = {
            k: v
            for k, v in {
                "version": version,
                "business_unit": business_unit,
                "environment": environment,
            }.items()
            if v is not None
        }
        if query:
            link += "?" + urllib.parse.urlencode(query)
        raw = self._request("GET", link)
        self._model(PatternDescription, raw)
        return raw

    def validate(self, body):
        raw = self._request("POST", self._bootstrap().links.validation, _json_body(body))
        self._model(ValidationResult, raw)
        return raw

    def submit(self, body, key):
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise ClientError("Invalid idempotency key")
        discovery = self._bootstrap()
        if discovery.capabilities.get("idempotent_intents") is False:
            raise ClientError("Server does not support idempotent intents")
        payload = _json_body(body)
        intent = Intent.model_validate(json.loads(payload))
        raw = self._request("POST", discovery.links.submit, payload, key, 202, mutation=True)
        try:
            operation = self._operation(raw)
        except ClientError:
            raise self._ambiguous(raw) from None
        if (
            operation.action != intent.action
            or operation.pattern != intent.pattern
            or (intent.resource_id is not None and operation.resource_id != intent.resource_id)
        ):
            raise AmbiguousMutation()
        return raw

    def _ambiguous(self, raw, known_operation_id=None):
        operation_id = known_operation_id or (raw.get("id") if isinstance(raw, dict) else None)
        if not isinstance(operation_id, str) or not OPERATION_ID.fullmatch(operation_id):
            operation_id = None
        return AmbiguousMutation(operation_id)

    def _operation_url(self, operation_id):
        if not isinstance(operation_id, str) or not OPERATION_ID.fullmatch(operation_id):
            raise ClientError("Invalid operation ID")
        return self._bootstrap().links.operations.rstrip("/") + "/" + operation_id

    def _operation(self, raw, operation_id=None):
        operation = self._model(Operation, raw)
        if operation_id and operation.id != operation_id:
            raise ClientError("Operation identity mismatch")
        expected_action = {
            "planned": "execute_with_plan_digest",
            "succeeded": "done",
            "failed": "inspect_failure",
            "uncertain": "reconcile_with_operator",
        }.get(operation.state, "poll")
        if (
            operation.terminal != (operation.state in {"succeeded", "failed", "uncertain"})
            or operation.next_action != expected_action
        ):
            raise ClientError("Incoherent operation state")
        base = self._operation_url(operation.id)
        for name, suffix in (("self", ""), ("events", "/events"), ("execute", "/execute")):
            try:
                actual = self._url(operation.links[name])
            except (KeyError, ClientError):
                raise ClientError("Operation link mismatch") from None
            if actual != self._url(base + suffix):
                raise ClientError("Operation link mismatch")
        return operation

    def _resource_url(self, resource_id):
        if not isinstance(resource_id, str) or not RESOURCE_ID.fullmatch(resource_id):
            raise ClientError("Invalid resource ID")
        link = self._bootstrap().links.resources
        if link is None:
            raise ClientError("Server does not support resource inventory")
        return link.rstrip("/") + "/" + resource_id

    def _resource(self, raw, resource_id=None):
        resource = self._model(Resource, raw)
        if resource_id and resource.id != resource_id:
            raise ClientError("Resource identity mismatch")
        base = self._resource_url(resource.id)
        try:
            self_link = self._url(resource.links["self"])
            latest = self._url(resource.links["latest_operation"])
        except (KeyError, ClientError):
            raise ClientError("Resource link mismatch") from None
        if self_link != self._url(base) or latest != self._url(
            self._operation_url(resource.latest_operation_id)
        ):
            raise ClientError("Resource link mismatch")
        return resource

    @staticmethod
    def _model(model, raw):
        try:
            return model.model_validate(raw)
        except (ValidationError, TypeError):
            raise ClientError("Invalid server response") from None

    def status(self, operation_id, wait=0):
        if type(wait) is not int or not 0 <= wait <= 30:
            raise ClientError("Invalid wait")
        link = self._operation_url(operation_id)
        if wait:
            if self._bootstrap().capabilities.get("operation_long_poll") is not True:
                raise ClientError("Server does not support long-poll status")
            link += "?" + urllib.parse.urlencode({"wait": wait})
        raw = self._request("GET", link, timeout=(wait + 5) if wait else None)
        self._operation(raw, operation_id)
        return raw

    def operations(self, *, limit=20, offset=0, before=None, resource_id=None, state=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ClientError("Invalid operation limit")
        if type(offset) is not int or offset < 0:
            raise ClientError("Invalid operation offset")
        if before is not None and (
            not isinstance(before, str) or not OPERATION_ID.fullmatch(before) or offset
        ):
            raise ClientError("Invalid operation cursor")
        if resource_id is not None and (
            not isinstance(resource_id, str) or not RESOURCE_ID.fullmatch(resource_id)
        ):
            raise ClientError("Invalid resource filter")
        if state is not None and state not in OPERATION_STATES:
            raise ClientError("Invalid state filter")
        discovery = self._bootstrap()
        if (
            before is not None
            and discovery.capabilities.get("stable_operation_pagination") is not True
        ):
            raise ClientError("Server does not support stable operation pagination")
        if (resource_id is not None or state is not None) and discovery.capabilities.get(
            "operation_filters"
        ) is not True:
            raise ClientError("Server does not support operation filters")
        query = {"limit": limit, "offset": offset}
        if before is not None:
            query["before"] = before
        if resource_id is not None:
            query["resource_id"] = resource_id
        if state is not None:
            query["state"] = state
        link = discovery.links.operations
        link += ("&" if "?" in link else "?") + urllib.parse.urlencode(query)
        raw = self._request("GET", link)
        page = self._model(OperationPage, raw)
        ids = [self._operation(item).id for item in raw["items"]]
        if len(ids) != len(set(ids)) or (before is not None and before in ids):
            raise ClientError("Invalid server response")
        if page.next_before is not None and (not ids or page.next_before != ids[-1]):
            raise ClientError("Invalid server response")
        if page.next_offset is not None and (
            before is not None
            or not ids
            or type(raw["next_offset"]) is not int
            or raw["next_offset"] != offset + len(ids)
        ):
            raise ClientError("Invalid server response")
        return raw

    def patterns(self):
        discovery = self._bootstrap()
        if discovery.capabilities.get("pattern_listing") is not True or not discovery.links.catalog:
            raise ClientError("Server does not support pattern listing")
        raw = self._request("GET", discovery.links.catalog)
        self._model(PatternList, raw)
        return raw

    def resources(
        self, *, limit=20, after=None, pattern=None, environment=None, state=None, label=None
    ):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ClientError("Invalid resource limit")
        if after is not None and (not isinstance(after, str) or not RESOURCE_ID.fullmatch(after)):
            raise ClientError("Invalid resource cursor")
        for value in (pattern, environment):
            if value is not None and (not isinstance(value, str) or not value):
                raise ClientError("Invalid resource filter")
        if state is not None and state not in RESOURCE_STATES:
            raise ClientError("Invalid state filter")
        if label is not None and (not isinstance(label, str) or "=" not in label):
            raise ClientError("Invalid label filter")
        discovery = self._bootstrap()
        if discovery.capabilities.get("resource_inventory") is not True:
            raise ClientError("Server does not support resource inventory")
        link = discovery.links.resources
        if link is None:
            raise ClientError("Server does not support resource inventory")
        filters = {"pattern": pattern, "environment": environment, "state": state}
        filters = {k: v for k, v in filters.items() if v is not None}
        if filters and discovery.capabilities.get("resource_filters") is not True:
            raise ClientError("Server does not support resource filters")
        if label is not None:
            if discovery.capabilities.get("resource_labels") is not True:
                raise ClientError("Server does not support resource labels")
            filters["label"] = label
        query = {"limit": limit, **filters}
        if after is not None:
            query["after"] = after
        link += ("&" if "?" in link else "?") + urllib.parse.urlencode(query)
        raw = self._request("GET", link)
        page = self._model(ResourcePage, raw)
        ids = [self._resource(item).id for item in raw["items"]]
        if len(ids) != len(set(ids)) or (after is not None and after in ids):
            raise ClientError("Invalid server response")
        if page.next_after is not None and (not ids or page.next_after != ids[-1]):
            raise ClientError("Invalid server response")
        return raw

    def resource(self, resource_id):
        discovery = self._bootstrap()
        if discovery.capabilities.get("resource_inventory") is not True:
            raise ClientError("Server does not support resource inventory")
        raw = self._request("GET", self._resource_url(resource_id))
        self._resource(raw, resource_id)
        return raw

    def execute(self, operation_id, digest):
        try:
            Execute.model_validate({"plan_digest": digest})
        except ValidationError:
            raise ClientError("Invalid plan digest") from None
        discovery = self._bootstrap()
        if discovery.capabilities.get("exact_plan_execution") is False:
            raise ClientError("Server does not support exact plan execution")
        current = self.status(operation_id)
        operation = self._operation(current, operation_id)
        if operation.state not in {"planned", "apply_queued", "applying", "succeeded"}:
            raise ClientError("Operation is not executable; inspect its state")
        if operation.plan_digest != digest:
            raise ClientError("Plan digest differs from the reviewed plan")
        raw = self._request(
            "POST",
            operation.links["execute"],
            json.dumps({"plan_digest": digest}).encode(),
            expected=202,
            mutation=True,
            known_operation_id=operation_id,
        )
        try:
            completed = self._operation(raw, operation_id)
            if completed.resource_id != operation.resource_id or completed.plan_digest != digest:
                raise ClientError("Execution response identity mismatch")
        except ClientError:
            raise self._ambiguous(raw, operation_id) from None
        return raw

    def discard(self, operation_id):
        discovery = self._bootstrap()
        if discovery.capabilities.get("discard_planned_operation") is not True:
            raise ClientError("Server does not support discarding a planned operation")
        current = self.status(operation_id)
        operation = self._operation(current, operation_id)
        raw = self._request(
            "POST",
            operation.links["discard"],
            expected=200,
            mutation=True,
            known_operation_id=operation_id,
        )
        try:
            completed = self._operation(raw, operation_id)
            if completed.resource_id != operation.resource_id:
                raise ClientError("Discard response identity mismatch")
        except ClientError:
            raise self._ambiguous(raw, operation_id) from None
        return raw

    def reconcile(self, operation_id, outcome, reason):
        try:
            Reconcile.model_validate({"outcome": outcome, "reason": reason})
        except ValidationError:
            raise ClientError("Invalid reconcile outcome or reason") from None
        discovery = self._bootstrap()
        if discovery.capabilities.get("operator_reconciliation") is not True:
            raise ClientError("Server does not support operator reconciliation")
        current = self.status(operation_id)
        operation = self._operation(current, operation_id)
        raw = self._request(
            "POST",
            operation.links["reconcile"],
            json.dumps({"outcome": outcome, "reason": reason}).encode(),
            expected=200,
            mutation=True,
            known_operation_id=operation_id,
        )
        try:
            completed = self._operation(raw, operation_id)
            if completed.resource_id != operation.resource_id:
                raise ClientError("Reconcile response identity mismatch")
        except ClientError:
            raise self._ambiguous(raw, operation_id) from None
        return raw

    def events(self, operation_id, *, after=0, limit=20):
        if type(after) is not int or not 0 <= after <= 2**63 - 1:
            raise ClientError("Invalid event position")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ClientError("Invalid event limit")
        if self._bootstrap().capabilities.get("operation_events") is False:
            raise ClientError("Server does not support operation events")
        operation = self._operation(self.status(operation_id), operation_id)
        link = (
            operation.links["events"]
            + "?"
            + urllib.parse.urlencode({"after": after, "limit": limit})
        )
        raw = self._request("GET", link)
        page = self._model(EventPage, raw)
        previous = after
        for event in raw["items"]:
            if (
                event["operation_id"] != operation_id
                or type(event["seq"]) is not int
                or event["seq"] <= previous
            ):
                raise ClientError("Invalid event page")
            previous = event["seq"]
        if raw["next_after"] is not None and (
            type(raw["next_after"]) is not int or not page.items or raw["next_after"] != previous
        ):
            raise ClientError("Invalid event page")
        return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("discover")
    describe = commands.add_parser("describe")
    describe.add_argument("pattern")
    for flag in ("version", "business-unit", "environment"):
        describe.add_argument(f"--{flag}")
    validate = commands.add_parser("validate")
    validate.add_argument("--body", type=Path, required=True)
    submit = commands.add_parser("submit")
    submit.add_argument("--body", type=Path, required=True)
    submit.add_argument("--key", required=True)
    operations = commands.add_parser("operations")
    operations.add_argument("--limit", type=int, default=20)
    operations.add_argument("--offset", type=int, default=0)
    operations.add_argument("--before")
    operations.add_argument("--resource-id")
    operations.add_argument("--state")
    status = commands.add_parser("status")
    status.add_argument("operation_id")
    status.add_argument("--wait", type=int, default=0)
    events = commands.add_parser("events")
    events.add_argument("operation_id")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--limit", type=int, default=20)
    execute = commands.add_parser("execute")
    execute.add_argument("operation_id")
    execute.add_argument("--digest", required=True)
    commands.add_parser("discard").add_argument("operation_id")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("operation_id")
    reconcile.add_argument("--outcome", required=True, choices=["succeeded", "failed"])
    reconcile.add_argument("--reason", required=True)
    resources = commands.add_parser("resources")
    resources.add_argument("--after")
    resources.add_argument("--limit", type=int, default=20)
    resources.add_argument("--pattern")
    resources.add_argument("--environment")
    resources.add_argument("--state", choices=sorted(RESOURCE_STATES))
    resources.add_argument("--label", help="key=value")
    commands.add_parser("patterns")
    commands.add_parser("resource").add_argument("resource_id")
    args = parser.parse_args(argv)
    try:
        client = Client(args.url, token=os.environ.get(TOKEN_ENV))
        if args.command == "discover":
            result = client.discover()
        elif args.command == "describe":
            result = client.describe(
                args.pattern, args.version, args.business_unit, args.environment
            )
        elif args.command == "validate":
            result = client.validate(args.body.read_bytes())
        elif args.command == "submit":
            result = client.submit(args.body.read_bytes(), args.key)
        elif args.command == "operations":
            result = client.operations(
                limit=args.limit,
                offset=args.offset,
                before=args.before,
                resource_id=args.resource_id,
                state=args.state,
            )
        elif args.command == "status":
            result = client.status(args.operation_id, wait=args.wait)
        elif args.command == "execute":
            result = client.execute(args.operation_id, args.digest)
        elif args.command == "discard":
            result = client.discard(args.operation_id)
        elif args.command == "reconcile":
            result = client.reconcile(args.operation_id, args.outcome, args.reason)
        elif args.command == "resources":
            result = client.resources(
                limit=args.limit,
                after=args.after,
                pattern=args.pattern,
                environment=args.environment,
                state=args.state,
                label=args.label,
            )
        elif args.command == "patterns":
            result = client.patterns()
        elif args.command == "resource":
            result = client.resource(args.resource_id)
        else:
            result = client.events(args.operation_id, after=args.after, limit=args.limit)
        print(json.dumps(result))
        return 0
    except (ClientError, OSError) as error:
        print(
            error if isinstance(error, ClientError) else "Unable to read request body",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
