"""OpenAPI quality for agent tool generation: metadata only, no behavior change.

operationIds are frozen; this is the exact set that existed in tests/fixtures/v1/openapi.json
before the metadata-only change that added summaries, descriptions, tags and examples.
"""

from fastapi.testclient import TestClient

from app.contracts import (
    AppApprove,
    AppFailover,
    AppIntent,
    Execute,
    Intent,
    Promote,
    Reconcile,
    Upgrade,
)
from app.main import app
from app.team_admin import (
    TeamArchive,
    TeamCreate,
    TeamImport,
    TeamReplace,
    TeamRevert,
    TeamUnarchive,
    TeamValidate,
)

client = TestClient(app)

FROZEN_V1_OPERATION_IDS = {
    "v1_approve_app",
    "v1_destroy_app",
    "v1_discard_app",
    "v1_failover_app",
    "v1_get_app",
    "v1_list_apps",
    "v1_submit_app",
    "v1_describe_pattern_v1_patterns__name__get",
    "v1_discard_operation_v1_operations__operation_id__discard_post",
    "v1_discover_v1_agent_get",
    "v1_execute_plan",
    "v1_get_operation",
    "v1_get_resource_v1_resources__resource_id__get",
    "v1_list_operations",
    "v1_list_patterns_v1_patterns_get",
    "v1_pattern_changes_v1_patterns__name__changes_get",
    "v1_pattern_check_v1_patterns__name__check_get",
    "v1_list_resources_v1_resources_get",
    "v1_operation_events_v1_operations__operation_id__events_get",
    "v1_reconcile_operation_v1_operations__operation_id__reconcile_post",
    "v1_submit_intent",
    "v1_check_drift",
    "v1_promote_resource",
    "v1_upgrade_resource",
    "v1_budget_history_v1_budgets_history_get",
    "v1_validate_intent_v1_intents_validate_post",
    "v1_admin_list_teams",
    "v1_admin_get_team",
    "v1_admin_validate_team",
    "v1_admin_create_team",
    "v1_admin_replace_team",
    "v1_admin_list_team_revisions",
    "v1_admin_get_team_revision",
    "v1_admin_revert_team",
    "v1_admin_archive_team",
    "v1_admin_unarchive_team",
    "v1_admin_import_teams",
}

MUTATING_METHODS = {"post", "put", "patch", "delete"}
REQUEST_MODELS = {
    "Intent": Intent,
    "Execute": Execute,
    "Reconcile": Reconcile,
    "AppIntent": AppIntent,
    "AppApprove": AppApprove,
    "AppFailover": AppFailover,
    "Promote": Promote,
    "Upgrade": Upgrade,
    "TeamValidate": TeamValidate,
    "TeamCreate": TeamCreate,
    "TeamReplace": TeamReplace,
    "TeamRevert": TeamRevert,
    "TeamArchive": TeamArchive,
    "TeamUnarchive": TeamUnarchive,
    "TeamImport": TeamImport,
}


def document():
    response = client.get("/v1/openapi.json")
    assert response.status_code == 200
    return response.json()


def test_operation_ids_are_unchanged():
    doc = document()
    ids = {
        op["operationId"]
        for methods in doc["paths"].values()
        for op in methods.values()
        if isinstance(op, dict) and "operationId" in op
    }
    assert ids == FROZEN_V1_OPERATION_IDS


def test_readyz_is_not_under_v1():
    doc = document()
    assert "/readyz" not in doc["paths"]
    full = client.get("/openapi.json").json()
    assert "/readyz" in full["paths"]


def test_every_operation_has_summary_description_and_tag():
    doc = document()
    for path, methods in doc["paths"].items():
        for method, op in methods.items():
            if not isinstance(op, dict) or "operationId" not in op:
                continue
            assert op.get("summary"), f"{method.upper()} {path} missing summary"
            assert op.get("description"), f"{method.upper()} {path} missing description"
            assert op.get("tags"), f"{method.upper()} {path} missing tags"


def _missing_descriptions(schema, schemas, name, seen):
    """Every property of `schema`, and of every schema it references, missing a description."""
    missing = []
    if "$ref" in schema:
        ref = schema["$ref"].split("/")[-1]
        if ref not in seen:
            seen.add(ref)
            missing += _missing_descriptions(schemas[ref], schemas, ref, seen)
        return missing
    for key in ("allOf", "anyOf", "oneOf"):
        for sub in schema.get(key, []):
            missing += _missing_descriptions(sub, schemas, name, seen)
    if "items" in schema:
        missing += _missing_descriptions(schema["items"], schemas, name, seen)
    for prop_name, prop_schema in schema.get("properties", {}).items():
        if "description" not in prop_schema:
            missing.append(f"{name}.{prop_name}")
        missing += _missing_descriptions(prop_schema, schemas, f"{name}.{prop_name}", seen)
    return missing


def test_every_schema_property_has_a_description():
    doc = document()
    schemas = doc["components"]["schemas"]
    missing = []
    for schema_name, schema in schemas.items():
        missing += _missing_descriptions(schema, schemas, schema_name, set())
    assert missing == []


def test_every_request_body_has_an_example_that_validates():
    doc = document()
    checked = 0
    for path, methods in doc["paths"].items():
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            body = op.get("requestBody")
            if not body:
                continue
            ref = body["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
            schema = doc["components"]["schemas"][ref]
            examples = schema.get("examples")
            assert examples, f"{method.upper()} {path} ({ref}) has no example"
            model = REQUEST_MODELS[ref]
            for example in examples:
                model.model_validate(example)
            checked += 1
    assert checked == 16
    # validate_intent, submit_intent, execute, reconcile, submit_app, approve_app, failover_app,
    # promote, upgrade, and the seven admin bodies (validate, create, replace, revert, archive,
    # unarchive, import)


def test_mutating_operations_document_error_responses():
    doc = document()
    for path, methods in doc["paths"].items():
        for method, op in methods.items():
            if method not in MUTATING_METHODS or not isinstance(op, dict):
                continue
            responses = op.get("responses", {})
            for code in ("409", "422", "503"):
                entry = responses.get(code)
                assert entry, f"{method.upper()} {path} missing {code} response"
                ref = entry["content"]["application/json"]["schema"]["$ref"]
                assert ref.endswith("ErrorEnvelope"), f"{method.upper()} {path} {code} wrong model"


def test_app_has_title_description_and_version():
    doc = document()
    assert doc["info"]["title"]
    assert doc["info"]["description"]
    assert doc["info"]["version"]


def test_idempotency_key_header_is_described():
    doc = document()
    op = doc["paths"]["/v1/operations"]["post"]
    (header,) = [p for p in op["parameters"] if p["name"] == "Idempotency-Key"]
    assert header.get("description")
