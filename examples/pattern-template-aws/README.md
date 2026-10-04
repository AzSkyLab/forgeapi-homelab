# pattern-template-aws

Copy-ready ForgeAPI pattern for AWS: a S3 bucket with versioning, public access block, SSE-S3 encryption, noncurrent-version lifecycle, and platform tags. Copy this directory into its own git repository.
The Azure template, `../pattern-template-azure`, is the full guide (inputs, secrets, versioning,
onboarding detail); this README only lists what differs.

## Inputs

| Variable | Who sets it | Notes |
|---|---|---|
| `name` | **Caller** | 3 to 63 lowercase letters, digits, hyphens; globally unique |
| `extra_tags` | **Caller** (optional) | Cannot override platform tags |
| `aws_account_id`, `region` | Platform | From the environment's cloud target. Callers cannot set them |
| `environment`, `business_unit`, `cost_center` | Platform | tags. Callers cannot set them |
| `noncurrent_retention_days` | Size | From `sizing` in `config.yaml` |

## Outputs

bucket_name, bucket_endpoint. Never the account or project ID. A secret-reference example is commented in `outputs.tf`.

## State

The platform only configures the `azurerm` backend. Without remote state, **version upgrades of a
deployment are refused**, because local state cannot be carried between runs. `versions.tf` has a
commented aws backend block: the platform team creates the state bucket, grants the worker
identity access, and decides the per-deployment state key or prefix before it is uncommented.

## Onboarding

1. `uv run python -m app.pattern_check /path/to/this-repo --cloud aws --terraform` (from the ForgeAPI repo)
2. `git tag v1.0.0 && git push origin v1.0.0` (never move a tag; update `CHANGELOG.md`)
3. `patterns.yaml`:

   ```yaml
   patterns:
     s3-bucket:
       repo: github.com/YOUR-ORG/terraform-aws-s3-bucket
       default_version: v1.0.0
       cloud: aws
   ```

4. `tenants.yaml`, business unit allow-list (environment names must match `sizing` and `estimated_costs`):

   ```yaml
   business_units:
     finance:
       patterns: [s3-bucket]
       inject:
         business_unit: finance
         cost_center: "CC-1042"
       environments:
         dev:
           targets:
             aws: {aws_account_id: "PLACEHOLDER", region: REGION}
           budget_monthly: 500
   ```

5. `GET /patterns/s3-bucket/check?version=v1.0.0`, deploy to dev, then promote to prd.

CI is `.github/workflows/pattern-ci.yml`: set the repository variable `FORGEAPI_REPO`
(owner/name of the ForgeAPI repo) and pin `FORGEAPI_REF`. No secrets are needed.
