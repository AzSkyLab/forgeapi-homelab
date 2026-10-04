# pattern-template-gcp

Copy-ready ForgeAPI pattern for GCP: a Cloud Storage bucket with uniform access, public access prevention enforced, versioning, noncurrent-version lifecycle, and platform labels. Copy this directory into its own git repository.
The Azure template, `../pattern-template-azure`, is the full guide (inputs, secrets, versioning,
onboarding detail); this README only lists what differs.

## Inputs

| Variable | Who sets it | Notes |
|---|---|---|
| `name` | **Caller** | 3 to 63 lowercase letters, digits, hyphens, underscores; globally unique |
| `extra_labels` | **Caller** (optional) | Cannot override platform labels |
| `project_id`, `region` | Platform | From the environment's cloud target. Callers cannot set them |
| `environment`, `business_unit`, `cost_center` | Platform | labels. Callers cannot set them |
| `noncurrent_retention_days` | Size | From `sizing` in `config.yaml` |

## Outputs

bucket_name, bucket_url. Never the account or project ID. A secret-reference example is commented in `outputs.tf`.

## State

The platform only configures the `azurerm` backend. Without remote state, **version upgrades of a
deployment are refused**, because local state cannot be carried between runs. `versions.tf` has a
commented gcp backend block: the platform team creates the state bucket, grants the worker
identity access, and decides the per-deployment state key or prefix before it is uncommented.

## Onboarding

1. `uv run python -m app.pattern_check /path/to/this-repo --cloud gcp --terraform` (from the ForgeAPI repo)
2. `git tag v1.0.0 && git push origin v1.0.0` (never move a tag; update `CHANGELOG.md`)
3. `patterns.yaml`:

   ```yaml
   patterns:
     gcs-bucket:
       repo: github.com/YOUR-ORG/terraform-google-gcs-bucket
       default_version: v1.0.0
       cloud: gcp
   ```

4. `tenants.yaml`, business unit allow-list (environment names must match `sizing` and `estimated_costs`):

   ```yaml
   business_units:
     finance:
       patterns: [gcs-bucket]
       inject:
         business_unit: finance
         cost_center: "CC-1042"
       environments:
         dev:
           targets:
             gcp: {project_id: "PLACEHOLDER", region: REGION}
           budget_monthly: 500
   ```

5. `GET /patterns/gcs-bucket/check?version=v1.0.0`, deploy to dev, then promote to prd.

CI is `.github/workflows/pattern-ci.yml`: set the repository variable `FORGEAPI_REPO`
(owner/name of the ForgeAPI repo) and pin `FORGEAPI_REF`. No secrets are needed.
