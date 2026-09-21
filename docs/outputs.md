# Outputs and secrets

**Question this answers:** a pattern creates a database; how does the caller get the connection string, now and next month?

## Decisions (engineer, 2026-09-21)

- Secrets live in **whatever Key Vault the pattern creates**. The platform has no vault of its own.
- The API returns **references only**. There is no endpoint that reveals a secret value.
- Consumers are apps with managed identities, pipelines/service principals and humans. Each reads the vault **with its own identity**.
- A deployment's secrets go when the deployment is destroyed (its vault is part of it).

## What the API does

- After every apply the worker reads `terraform output -json`. Non-sensitive outputs are stored on the deployment record and returned by `GET /deployments/{id}` for as long as the record exists. That is the "retrieve it later" mechanism: hostnames, resource IDs, URIs, names, **and secret references**.
- An output the pattern marks `sensitive` is **never stored or returned**: not in the record, the response, the log or the audit trail. Its **name** is listed in `withheld_outputs`, so a pattern that marks something sensitive without putting it in a vault is visible instead of silently lossy.
- `secret_references` lists every Key Vault secret ID found anywhere in the outputs, keyed by where it was found (`database_url_secret`, `secrets.pg_admin`). These are pointers, not secrets.

```json
{
  "outputs": { "host": "psql-x.postgres.database.azure.com", "database_url_secret": "https://kv-x.vault.azure.net/secrets/database-url" },
  "secret_references": { "database_url_secret": "https://kv-x.vault.azure.net/secrets/database-url" },
  "withheld_outputs": ["admin_password"]
}
```

Reading one: `az keyvault secret show --id <reference> --query value -o tsv`, a Key Vault reference in an App Service / Container App setting, or the SDK with a managed identity.

## Convention for pattern authors

1. **Prefer no secret at all.** Postgres, Azure SQL, Storage, Service Bus and Key Vault support Entra authentication: give the consuming identity a data-plane role and output only the host and database names.
2. **When a secret must exist, the pattern stores it** in a Key Vault it creates (`terraform-pattern-web-backend` already does: `database-url`, `pg-admin-password`) and **outputs the secret's ID**, preferably versionless so rotation does not break consumers. Do not output the value, even marked `sensitive`: the API will withhold it and nobody will be able to use it.
3. **Grant read access in the pattern.** The existing patterns create `…-secrets-readers` / `…-secrets-admins` Entra groups with the request's `owners` as group owners; owners then add the humans, pipelines and app identities that need access. A workload created by the same pattern gets `Key Vault Secrets User` directly on its managed identity. The platform mapping can also inject reader principals (any `inject:` key a pattern declares as a variable), for example the business unit's group.
4. **Name outputs for what they are:** `*_secret` / a `secrets` map for references, plain names for plain values.
5. Anything `sensitive` that is *not* a reference will show up in `withheld_outputs`. Treat that as a pattern bug to fix.

## Worked example

`key-vault` v1.2.0 (`AzSkyLab/terraform-azurerm-key-vault`, `pattern/`): input `generated_secret_names: ["database-url", "api-key"]` creates random secrets inside the pattern's vault; output `secrets` holds only the versionless references. Read access follows the pattern's own model: the request's `owners` own the `…-secrets-readers` group and add whoever needs to read.

## Limits

- Sensitive values still exist in Terraform state (every Terraform setup has this); the state container is readable only by the identity Terraform runs as.
- Sensitive **inputs** are still stored in clear on the deployment record (see [audit.md](audit.md)); pass secrets in as Key Vault references too.
- The API does not check that a referenced secret exists or that the caller can read it.
- Outputs are as of the last apply. A secret rotated in the vault keeps its versionless reference; anything else that changes outside Terraform is not reflected until the next apply.
