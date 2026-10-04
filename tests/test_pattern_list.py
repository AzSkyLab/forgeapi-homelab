"""GET /patterns lists what this caller can use, cheaply and without versions."""

import pytest

from app.main import app, caller_context
from app.tenants import Caller
from tests.test_operation_policy import placed as placed
from tests.test_operations import agent_client as agent_client


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_open_mode_lists_every_pattern_with_links(agent_client, prefix):
    listing = agent_client.get(f"{prefix}/patterns").json()
    assert [i["name"] for i in listing["items"]] == ["demo", "local-file"]
    demo = listing["items"][0]
    assert demo["links"] == {"self": f"{prefix}/patterns/{demo['name']}"}
    assert set(demo) == {"name", "cloud", "links"}
    assert agent_client.get(demo["links"]["self"]).status_code == 200
    discovery = agent_client.get(f"{prefix}/agent").json()
    assert discovery["links"]["catalog"] == f"{prefix}/patterns"
    assert discovery["links"]["patterns"] == f"{prefix}/patterns/{{name}}"
    assert discovery["capabilities"]["pattern_listing"] is True


def test_tenant_restricted_caller_sees_only_allowed_patterns(placed):
    assert [i["name"] for i in placed.get("/patterns").json()["items"]] == ["demo"]
    app.dependency_overrides[caller_context] = lambda: Caller("nobody", groups=frozenset())
    try:
        assert placed.get("/patterns").json()["items"] == []
    finally:
        app.dependency_overrides.clear()
