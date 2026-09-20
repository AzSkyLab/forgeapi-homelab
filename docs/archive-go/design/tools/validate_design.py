"""Repository-relative design/contract checks; no application or cloud effects."""
from pathlib import Path
import copy
import hashlib
import json
import re
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[3]
DESIGN = ROOT / 'docs/design'

class UniqueLoader(yaml.SafeLoader):
    pass

def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise AssertionError(f'Duplicate YAML key: {key}')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result

UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
spec = yaml.load((DESIGN / 'openapi.yaml').read_text(), Loader=UniqueLoader)
assert spec['openapi'] == '3.1.1'
assert spec['security'] == [{'EntraBearer': []}]

# RF-13: valid YAML can still produce the wrong schema structure.
download = spec['components']['schemas']['Artifact']['properties']['download_url']
assert set(download) == {'type', 'format', 'description'}, download
assert download['description'] == 'Same-origin authenticated API route, never a cloud signed URL.'
assert spec['x-internal-lint-profile'] == 'platform-api-design/0.1.0'

def lookup(ref):
    assert ref.startswith('#/'), f'Unexpected remote reference: {ref}'
    item = spec
    for segment in ref[2:].split('/'):
        item = item[segment.replace('~1', '/').replace('~0', '~')]
    return item

def walk(item):
    if isinstance(item, dict):
        yield item
        for value in item.values():
            yield from walk(value)
    elif isinstance(item, list):
        for value in item:
            yield from walk(value)

refs = [item['$ref'] for item in walk(spec) if '$ref' in item]
for ref in refs:
    lookup(ref)

def resolve(item):
    if isinstance(item, dict):
        if '$ref' in item:
            return resolve({**lookup(item['$ref']), **{k: v for k, v in item.items() if k != '$ref'}})
        return {k: resolve(v) for k, v in item.items()}
    if isinstance(item, list):
        return [resolve(v) for v in item]
    return item

expected_permissions = {
    'getIdentityContext': [],
    'listExecutionTemplates': ['catalog.read'],
    'createExecution': ['execution.submit'],
    'createTemplateExecution': ['execution.submit'],
    'getExecution': ['execution.read'],
    'requestExecutionCancellation': ['execution.cancel'],
    'getExecutionEvents': ['execution.read'],
    'getExecutionLogs': ['execution.read', 'execution.data.read'],
    'getExecutionResults': ['execution.read', 'execution.data.read'],
    'downloadExecutionArtifact': ['execution.read', 'execution.data.read'],
}
operations = []
for path, path_item in spec['paths'].items():
    for method, operation in path_item.items():
        if method not in {'get', 'post', 'put', 'patch', 'delete', 'head', 'options'}:
            continue
        operations.append(operation['operationId'])
        assert operation['responses'], path
        permissions = operation.get('x-required-permissions', [])
        assert permissions == expected_permissions[operation['operationId']], path
        assert operation.get('security', spec['security']) == [{'EntraBearer': permissions}], path
        assert set(permissions) <= {'catalog.read', 'execution.submit', 'execution.read', 'execution.cancel', 'execution.data.read'}, path
        params = [resolve(p) for p in path_item.get('parameters', []) + operation.get('parameters', [])]
        required_paths = {p['name'] for p in params if p['in'] == 'path' and p.get('required')}
        assert set(re.findall(r'\{([^}]+)\}', path)) == required_paths, path
        if method == 'post':
            assert any(p['name'] == 'Idempotency-Key' and p['required'] for p in params), path
            body = resolve(operation['requestBody'])
            assert body['required'] == (operation['operationId'] != 'requestExecutionCancellation'), path
        for response in operation['responses'].values():
            resolved_response = resolve(response)
            assert {'X-Request-ID', 'X-Flow-ID'} <= resolved_response.get('headers', {}).keys(), path
        assert {'406', '429', '503'} <= operation['responses'].keys(), path
        assert {'X-Flow-ID', 'traceparent', 'tracestate'} <= {p['name'] for p in params}, path
assert len(operations) == len(set(operations))
assert set(operations) == set(expected_permissions)

schemas = spec['components']['schemas']
for name, schema in schemas.items():
    Draft202012Validator.check_schema(resolve(schema))

example_count = 0
for item in walk(spec):
    if 'schema' in item and 'example' in item:
        Draft202012Validator(resolve(item['schema']), format_checker=FormatChecker()).validate(item['example'])
        example_count += 1

request = copy.deepcopy(spec['components']['requestBodies']['ExecutionInput']['content']['application/json']['example'])
validator = Draft202012Validator(resolve(schemas['ExecutionInput']), format_checker=FormatChecker())
validator.validate(request)
required = {'application_id', 'environment', 'template_id', 'template_version', 'input_artifact_refs'}
assert set(schemas['ExecutionInput']['required']) == required
minimal_request = {key: request[key] for key in required}
validator.validate(minimal_request)
negative_inputs = []
for key in sorted(required):
    invalid = {k: v for k, v in minimal_request.items() if k != key}
    assert not validator.is_valid(invalid), key
    negative_inputs.append('missing-' + key)
for key in sorted(set(request) - required):
    assert not validator.is_valid({**minimal_request, key: None}), key
    negative_inputs.append('null-' + key)
for key, value in [('subscription_id', 'hidden-cloud-id'), ('image', 'registry.example.com/reviewer:latest'), ('timeout_seconds', 0), ('execution_class', 'arbitrary-vm'), ('secret_refs', ['https://vault.example.com/secret'])]:
    invalid = {**request, key: value}
    assert not validator.is_valid(invalid), key
    negative_inputs.append(key)
invalid = copy.deepcopy(request)
invalid['environment_variables'] = {f'KEY_{i}': 'value' for i in range(33)}
assert not validator.is_valid(invalid)
negative_inputs.append('environment-count')
accepted = spec['components']['responses']['ExecutionAccepted']['content']['application/json']['example']
Draft202012Validator(resolve(schemas['Execution']), format_checker=FormatChecker()).validate({**accepted, 'future_field': 'compatible'})

forbidden_fields = {'provider', 'provider_reference', 'subscription_id', 'tenant_id', 'resource_group', 'pool_id', 'node_id', 'vm_size', 'subnet_id', 'managed_identity_id'}
public_props = {key for item in walk(spec) for key in item.get('properties', {})}
assert not public_props & forbidden_fields, public_props & forbidden_fields

files = (sorted((ROOT / 'docs/design').glob('*.md'))
         + sorted((ROOT / 'docs/adr').glob('*.md'))
         + [ROOT / 'docs/review/m0-design-review-response-2026-09-07.md'])
missing = []
checked_links = 0
for file in files:
    text = file.read_text()
    assert text.count('```') % 2 == 0, f'Unbalanced code fences in {file}'
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
        if re.match(r'https?://', target):
            continue
        target_path, _, anchor = target.partition('#')
        destination = (file.parent / target_path).resolve() if target_path else file
        if not destination.exists():
            missing.append(f'{file.relative_to(ROOT)} -> {target}')
        else:
            checked_links += 1
            if anchor:
                headings = re.findall(r'^#{1,6}\s+(.+)$', destination.read_text(), re.M)
                slugs = [re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-') for heading in headings]
                assert anchor in slugs, f'Invalid anchor: {file} -> {target}'
    if file.parent.name == 'adr':
        for section in ['Context', 'Consequences', 'Verification dependencies']:
            assert f'## {section}' in text, f'{file}: missing {section}'
        assert '**Status:** Proposed' in text, file

assert not missing, '\n'.join(missing)
for number in range(1, 13):
    assert len(list(DESIGN.glob(f'{number:02d}-*.md'))) == 1
source_hashes = {
    'combined-build-prompt.md': 'cdf49def46bc139117d6837008720ed587317145cf401d261d490b44f2c00017',
    'poc-intent-and-requirements.md': '431a8a0b2d7334b33801396569ae4ba8ca0b9ea4cee173ab2a2be033adc621e6',
}
for name, expected in source_hashes.items():
    assert hashlib.sha256((ROOT / 'docs' / name).read_bytes()).hexdigest() == expected

delivery = (DESIGN / '11-delivery.md').read_text()
questions = (DESIGN / '12-verification.md').read_text()
for number in range(1, 26):
    assert re.search(rf'^\| {number} ', delivery, re.M), f'POC mapping {number}'
for number in range(1, 16):
    assert re.search(rf'^\| Q{number} ', questions, re.M), f'Question mapping {number}'
for number in range(1, 19):
    assert f'| F{number:02d} |' in delivery

# Schema-level regressions; these do not execute template policy or runtime behavior.
event_fields = {'state', 'cleanup_state', 'delivery_status', 'infrastructure_status',
                'dispatch_status', 'result_complete'}
assert event_fields <= set(schemas['ExecutionEvent']['required'])
assert 'name' in schemas['Artifact']['required']
assert 'deadline_at' in schemas['Cancellation']['required']
example_responses = 0
for path_item in spec['paths'].values():
    for method, operation in path_item.items():
        if method not in {'get', 'post'}:
            continue
        response = resolve(operation['responses'].get('200', operation['responses'].get('202')))
        media = response.get('content', {}).get('application/json')
        if media:
            assert 'example' in media, operation['operationId']
            example_responses += 1
log_media = spec['paths']['/executions/{execution_id}/logs']['get']['responses']['200']['content']['application/json']
assert {'items', 'cursor', 'page', 'truncated'} <= set(log_media['schema']['required'])
log_validator = Draft202012Validator(resolve(log_media['schema']), format_checker=FormatChecker())
assert log_validator.is_valid({'items': [], 'cursor': 'tail-position', 'page': {}, 'truncated': False})
assert not log_validator.is_valid({'items': [], 'page': {}, 'truncated': False})
event_validator = Draft202012Validator(resolve(schemas['ExecutionEvent']), format_checker=FormatChecker())
event_example = spec['paths']['/executions/{execution_id}/events']['get']['responses']['200']['content']['application/json']['example']['items'][0]
assert event_validator.is_valid({**event_example, 'event_type': 'execution.future_event', 'future_field': 1})
assert not event_validator.is_valid({k: v for k, v in event_example.items() if k != 'delivery_status'})
artifact_example = spec['paths']['/executions/{execution_id}/results']['get']['responses']['200']['content']['application/json']['example']['artifacts'][0]
artifact_validator = Draft202012Validator(resolve(schemas['Artifact']), format_checker=FormatChecker())
assert not artifact_validator.is_valid({k: v for k, v in artifact_example.items() if k != 'name'})
template_example = spec['paths']['/execution-templates']['get']['responses']['200']['content']['application/json']['example']['items'][0]
assert set(template_example['allowed_overrides']) == {'compute_profile', 'timeout_seconds'}
assert set(template_example['defaults']) <= schemas['ExecutionInput']['properties'].keys()
for key, value in template_example['defaults'].items():
    assert request[key] == value, key
Draft202012Validator.check_schema(template_example['input_schema'])
template_validator = Draft202012Validator(template_example['input_schema'])
template_validator.validate(request)
template_validator.validate(minimal_request)
assert not template_validator.is_valid({**request, 'timeout_seconds': 1801})
assert not template_validator.is_valid({**request, 'secret_refs': ['unapproved-secret']})
alias_paths = ['/executions', '/execution-templates/{template_id}/executions']
assert set(spec['paths'][alias_paths[0]]['post']['responses']) == set(spec['paths'][alias_paths[1]]['post']['responses'])
review_response = (ROOT / 'docs/review/m0-design-review-response-2026-09-07.md').read_text()
finding_ids = re.findall(r'^\| (RF-\d{2}) \|', review_response, re.M)
assert len(finding_ids) == len(set(finding_ids)) == 56
assert set(finding_ids) == {f'RF-{n:02d}' for n in range(1, 57)}
assert '| A01 |' in delivery and '| A02 |' in delivery
assert '| S11 |' in delivery

# The Azure-specific lab contract is separate; do not relax the portable
# compute property denylist or its exact ten-operation regression above.
lab_spec = yaml.load((DESIGN / 'openapi-deployments.yaml').read_text(), Loader=UniqueLoader)
assert lab_spec['openapi'] == '3.1.1'
assert lab_spec['security'] == [{'EntraBearer': []}]
lab_expected = {
    ('get', '/deployment-patterns'): 'listDeploymentPatterns',
    ('post', '/deployments'): 'createDeployment',
    ('get', '/deployments/{deployment_id}'): 'getDeployment',
    ('post', '/deployments/{deployment_id}/approvals'): 'approveDeployment',
}

def resolve_lab(value):
    if isinstance(value, dict):
        if '$ref' in value:
            ref = value['$ref']
            assert ref.startswith('#/'), f'Unexpected external lab reference: {ref}'
            node = lab_spec
            for part in ref[2:].split('/'):
                node = node[part.replace('~1', '/').replace('~0', '~')]
            return resolve_lab({**node, **{k: v for k, v in value.items() if k != '$ref'}})
        return {k: resolve_lab(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_lab(v) for v in value]
    return value

resolve_lab(lab_spec)  # Every reference must resolve, including unused components.
lab_operations = {}
for path, path_item in lab_spec['paths'].items():
    for method, operation in path_item.items():
        if method not in {'get', 'post', 'put', 'patch', 'delete', 'head', 'options'}:
            continue
        lab_operations[(method, path)] = operation['operationId']
        assert operation.get('security', lab_spec['security']) == [{'EntraBearer': []}]
        params = [resolve_lab(p) for p in path_item.get('parameters', []) + operation.get('parameters', [])]
        assert set(re.findall(r'\{([^}]+)\}', path)) == {p['name'] for p in params if p['in'] == 'path' and p.get('required')}
        assert {'400', '401', '403', '404', '406', '503'} <= operation['responses'].keys()
        if method == 'post':
            assert {'409', '413', '415'} <= operation['responses'].keys()
            assert any(p['name'] == 'Idempotency-Key' and p['required'] for p in params)
            assert operation['requestBody']['required']
        for code, response in operation['responses'].items():
            headers = resolve_lab(response)['headers']
            assert {'X-Request-ID', 'X-Flow-ID', 'Cache-Control'} <= headers.keys()
            if code == '202':
                assert {'Location', 'Retry-After'} <= headers.keys()
            if code == '401':
                assert 'WWW-Authenticate' in headers
assert lab_operations == lab_expected
assert len(set(lab_operations.values())) == 4
router = (ROOT / 'internal/httpapi/api.go').read_text()
lab_routes = {(method.lower(), path) for method, path in re.findall(r'r\.(Get|Post)\("([^"\n]+)"', router) if path.startswith('/deployment')}
assert lab_routes == set(lab_expected), 'Lab OpenAPI/HTTP router drift'
for schema in lab_spec['components']['schemas'].values():
    Draft202012Validator.check_schema(resolve_lab(schema))
lab_examples = 0
for item in walk(lab_spec):
    if 'schema' in item and 'example' in item:
        Draft202012Validator(resolve_lab(item['schema']), format_checker=FormatChecker()).validate(item['example'])
        lab_examples += 1
lab_input = Draft202012Validator(resolve_lab(lab_spec['components']['schemas']['DeploymentInput']))
valid_lab_input = {'pattern_id': 'azure-key-vault-v1'}
lab_input.validate(valid_lab_input)
for invalid in [{}, {'pattern_id': None}, {'pattern_id': 'other'}] + [
    {**valid_lab_input, key: 'caller-override'} for key in ['target', 'executor', 'subscription_id', 'source', 'terraform']
]:
    assert not lab_input.is_valid(invalid), invalid
lab_approval = Draft202012Validator(resolve_lab(lab_spec['components']['schemas']['DeploymentApprovalInput']))
lab_approval.validate({'plan_digest': 'sha256:' + 'c' * 64})
for invalid in [{}, {'plan_digest': None}, {'plan_digest': 'wrong'}, {'plan_digest': 'sha256:' + 'c' * 64, 'target': {}}]:
    assert not lab_approval.is_valid(invalid), invalid
lab_props = {key for item in walk(lab_spec) for key in item.get('properties', {})}
assert not lab_props & {'certificate_path', 'private_key', 'client_secret', 'access_token', 'refresh_token'}

print(json.dumps({
    'markdown_files': len(files),
    'numbered_sections': 12,
    'adrs': len(list((ROOT / 'docs/adr').glob('*.md'))),
    'local_links_and_anchors': checked_links,
    'openapi_operations': len(operations),
    'lab_openapi_operations': len(lab_operations),
    'lab_embedded_examples_validated': lab_examples,
    'lab_input_and_router_regressions': True,
    'resolved_openapi_refs': len(refs),
    'component_schemas_checked': len(schemas),
    'embedded_examples_validated': example_count,
    'json_success_operations_with_examples': example_responses,
    'minimal_template_input_validated': True,
    'review_findings_dispositioned': len(finding_ids),
    'contract_regressions': ['RF-13 flow-mapping structure', 'required versus defaulted fields', 'null is not omission', 'all-response correlation headers', 'security permission roles', 'alias response parity', 'tail cursor at empty page', 'event status facets and extensibility', 'named artifacts', 'template example constraints'],
    'negative_structural_examples_rejected': negative_inputs,
    'response_extension_accepted': True,
    'forbidden_provider_properties_absent': True,
    'source_hashes_preserved': True,
    'poc_sections_mapped': 25,
    'poc_questions_mapped': 15,
    'failure_cases_registered': 18,
    'limitations': ['Separate pinned Redocly lint command required', 'Enterprise guideline profile unverified', 'No behavior, tenant or cloud tests', 'Mermaid syntax not rendered by this script']
}, indent=2))
