"""Rule lifting uses the exact condition shapes found in the real pattern repos."""

import pytest
import yaml

from app import schema
from tests.conftest import git

INPUTS = {"filename": "hello.txt", "content": "hi"}
FILENAME_RULE = "filename must be a plain file name (letters, digits, dot, dash, underscore)."
RG_NAME_RULE = '${can(regex("^[a-zA-Z0-9._()-]+$", var.name)) && length(var.name) <= 90}'


@pytest.mark.parametrize(
    ("name", "tf_type", "condition", "expected"),
    [
        (
            "environment",
            "string",
            '${contains(["prototype", "dev", "prd"], var.environment)}',
            {"enum": ["prototype", "dev", "prd"]},
        ),
        ("tier", "number", "${var.tier >= 1 && var.tier <= 4}", {"minimum": 1, "maximum": 4}),
        ("owners", "list(string)", "${length(var.owners) > 0}", {"minItems": 1}),
        (
            "name",
            "string",
            '${can(regex("^[a-zA-Z0-9._()-]+$", var.name)) && length(var.name) <= 90}',
            {"pattern": "^[a-zA-Z0-9._()-]+$", "maxLength": 90},
        ),
        ("ratio", "number", "${var.ratio > 0}", {"exclusiveMinimum": 0}),
    ],
)
def test_lifts_recognised_rules(name, tf_type, condition, expected):
    assert schema.lift(name, tf_type, condition) == expected


@pytest.mark.parametrize(
    "condition",
    [
        '${var.a == "x" || var.a == "y"}',  # `||`: parts are not individually required
        "${var.a != var.b}",  # unknown shape
        '${contains(["x"], var.other)}',  # about a different variable
        '${can(regex("([", var.a))}',  # not a valid regex here
        '${var.a == "p" ? true : length(var.a) > 3}',
    ],
)
def test_unrecognised_rules_are_left_to_terraform(condition):
    assert schema.lift("a", "string", condition) == {}


def test_operators_inside_string_literals_do_not_confuse_the_parser():
    lifted = schema.lift("a", "string", '${can(regex("^(x||y&&z)$", var.a)) && length(var.a) <= 9}')
    assert lifted == {"pattern": "^(x||y&&z)$", "maxLength": 9}


def test_pattern_page_has_rules_example_schema_link_and_about(client, pattern_repo):
    (pattern_repo / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "description": "Writes\n  a file.",
                "use_cases": ["demo"],
                "config": {"internal": True},
            }
        )
    )
    git(pattern_repo, "add", "."), git(pattern_repo, "commit", "-qm", "about")
    git(pattern_repo, "tag", "v1.2.0")

    page = client.get("/patterns/demo").json()

    assert page["about"] == {"description": "Writes a file.", "use_cases": ["demo"]}
    filename = next(v for v in page["inputs"] if v["name"] == "filename")
    assert filename["pattern"] == "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    assert filename["rules"] == [
        "filename must be a plain file name (letters, digits, dot, dash, underscore)."
    ]
    assert page["example"] == {
        "pattern": "demo",
        "version": "v1.2.0",
        "inputs": {"filename": "<filename>", "content": "<content>"},
    }
    assert page["links"]["schema"] == "/patterns/demo/schema?version=v1.2.0"
    assert client.get("/patterns/demo?version=v1.0.0").json()["about"] == {}


def test_json_schema_endpoint(client):
    body = client.get("/patterns/demo/schema").json()
    assert body["required"] == ["filename", "content"]
    assert body["additionalProperties"] is False
    assert body["properties"]["filename"]["pattern"].startswith("^[A-Za-z0-9]")
    assert body["properties"]["suffix"] == {"type": "string", "default": ""}
    assert "suffix" not in client.get("/patterns/demo/schema?version=v1.0.0").json()["properties"]


def test_lifted_rule_is_rejected_up_front_with_the_authors_message(client, dispatched):
    response = client.post(
        "/deployments", json={"pattern": "demo", "inputs": {**INPUTS, "filename": "../escape"}}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "field": "filename",
            "message": FILENAME_RULE,
        }
    ]
    assert "../escape" not in response.text
    assert dispatched == []


def test_dry_run_validates_and_creates_nothing(client, dispatched):
    ok = client.post("/deployments?dry_run=true", json={"pattern": "demo", "inputs": INPUTS})
    assert ok.status_code == 200
    assert ok.json()["valid"] is True and ok.json()["version"] == "v1.1.0"
    assert "id" not in ok.json()

    bad = client.post("/deployments?dry_run=true", json={"pattern": "demo", "inputs": {}})
    assert bad.status_code == 422
    assert {p["field"] for p in bad.json()["detail"]} == {None}
    assert "'filename' is a required property" in bad.text
    assert dispatched == []
