# Onboarding a Terraform pattern

A pattern is a Terraform **root module in its own git repository**, versioned by semver tags and
pinned to a commit for every deployment. ForgeAPI never contains pattern Terraform (only test
fixtures under `examples/`). Most ordinary root modules work, but the API enforces a contract;
this guide is that contract and the process to meet it before anyone deploys.

## The process

1. **Start from a template.** Copy `examples/pattern-template-azure` (the primary target; `-aws`
   and `-gcp` exist for other clouds) into a new repository. They already wire the platform's placement inputs into the provider, carry a
   `config.yaml` with costs and sizes, a README, a changelog and a CI workflow.
2. **Write the module** (rules below).
3. **Check it locally** from a ForgeAPI checkout:

   ```sh
   uv run python -m app.pattern_check ../my-pattern --cloud aws --terraform
   uv run python -m app.pattern_check https://github.com/org/my-pattern --ref v1.0.0 --cloud aws
   ```

   Errors mean the API would refuse or break it; warnings are risks to fix or accept knowingly.
   With no errors it prints the `patterns.yaml` entry and the business-unit allow-list line.
   Exit code 1 on errors, so the pattern repo's CI can gate on it.
4. **Tag** `v1.0.0` (never move or delete a published tag; a moved tag is refused with 409
   `revision_moved` for anything validated against the old commit).
5. **Register**: add the `patterns.yaml` entry (with `cloud`), allow it in each business unit's
   `patterns` list in the tenant mapping, and make sure every variable the pattern documents as
   platform-set is actually configured there (`inject` keys such as `business_unit` and
   `cost_center`, environment `network` keys such as `private_endpoint_subnet_id`). A variable
   is hidden from callers only when the mapping injects it; otherwise it shows up as a caller
   input (check `GET /patterns/{name}?business_unit=&environment=`). Validate the registry:
   `uv run python -m app.pattern_check --catalog patterns.yaml`.
6. **Check through the API** at the pinned commit: `GET /patterns/{name}/check?version=v1.0.0`
   (capability `pattern_checks`); the portal catalog shows the same **Contract** panel.
7. **Deploy to a dev landing zone** (real Azure at work): submit an intent, review the plan,
   apply, run a drift check, then promote to prod (`POST /resources/{id}/promote`, same commit).
   In the home lab the same flow runs against the free Floci emulators; that is verification
   only, not part of the contract.
8. **Release new versions** with a new tag and a changelog entry; consumers see
   `upgrade_available` and `GET /patterns/{name}/changes` shows commits and the input diff
   (new required inputs block one-click upgrades).

## The contract

### Repository and versions
- `patterns.yaml` entry: `repo` (`host/org/name` or a URL), optional `path`, `default_version`,
  `cloud` (`aws` | `azure` | `gcp`). Unknown keys are rejected. `local:` entries are for tests.
- Only `vX.Y.Z` tags count; pre-release and other tags are ignored. `default_version` must be an
  existing tag. Private repos use a GitHub App installation token (hosted) or a run-time token.

### Module shape
- Runnable root module (provider and backend blocks included) at the repo root or `path`.
- `required_version` must allow the platform's Terraform (1.16.5 today); pin providers (`~>`).
- No provisioners, `local-exec`, `external` or `http` data sources unless deliberately reviewed.

### Inputs
- Inputs are the root `variable` blocks. Supported caller types: string, number, bool,
  list(string), set(string), list(number), map(string); validation `error_message` is what
  callers see when they get it wrong.
- **No `sensitive = true` inputs**: they are refused. Accept a secret *reference* instead.
- **Placement inputs are injected and hidden from callers.** Declare exactly:
  aws `aws_account_id` + `region`; azure `subscription_id` + `region`; gcp `project_id` +
  `region`, **and wire them into the provider** (`allowed_account_ids`, `subscription_id`,
  `project`). Also injected if declared: the unit's `inject` keys (e.g. `business_unit`,
  `cost_center`), the environment's `network` keys, `environment`, and size variables.
- Never put placement IDs in resource names or `for_each` keys (planning fails as unsafe).

### Outputs
- Sensitive outputs are withheld (names listed in `withheld_outputs`); outputs containing
  placement IDs are withheld too. Output secret references, never values. Outputs must be
  JSON-safe (a non-finite number after apply makes the operation `uncertain`).

### Cost and sizes (`config.yaml`)
- `estimated_costs`: a number, per environment, or per size per environment; required in
  budgeted environments (`0` for free patterns).
- `sizing.<size>.<env>.<variable>: value`; every sizing variable must be declared, and sizes
  should match `estimated_costs`.

### State
- `backend "azurerm" {}` (empty) gets its configuration injected per deployment.
- Other backends must configure themselves; no backend means local state in the workspace, and
  **version upgrades are refused** for such deployments.

### Guardrails you will meet
- Environments may forbid destroy and protect resource types from deletion/replacement; a plan
  that would do either is refused (`policy_denied`).
- Plans run under an 8-minute deadline, applies under 28 minutes.

## What the checker reports

Errors (the API would refuse it or it breaks at runtime): `hcl_parse`, `no_resources`,
`required_version_excludes_platform`, `sensitive_input`, `missing_target_inputs`,
`hardcoded_placement` (a provider fixes the account/subscription/project instead of using the
injected variable), `config_invalid`, `invalid_estimated_costs`, `invalid_sizing`,
`sizing_var_undeclared`, `hardcoded_backend`, `terraform_init`/`terraform_validate` (with
`--terraform`), and the catalog checks (`unknown_catalog_key`, `invalid_cloud`, `local_and_repo`,
`no_source`, `repo_unreachable`, `no_versions`, `default_version_missing`, `path_missing`).

Warnings (risks): `no_required_version`, `unpinned_provider`, `undeclared_provider`,
`unchecked_type`, `target_not_wired`, `secret_like_output`, `output_reveals_placement`,
`no_estimated_costs`, `cost_sizing_mismatch`, `unknown_config_key`, `local_state`,
`self_configured_backend`, `remote_code` (provisioners, `external`/`http` data sources),
`placement_in_address`.

Info: `no_description`, `validation_runtime_only`, `location_and_target`, `sensitive_output`,
`platform_backend`, `nested_module_variables`, `non_semver_tags`.

The templates check clean: Azure with no warnings; AWS and GCP with `local_state` until their
commented remote-state block is configured. Not checked: provider behaviour at apply time,
outputs that are not JSON-safe (found after apply), `for_each` keys built through locals.

## Multi-cloud HA patterns
Replicas output `endpoint` and `health_path`; routers take `primary_endpoint`,
`secondary_endpoint`, `primary_health_path`, `secondary_health_path` ([ha-apps.md](ha-apps.md)).
