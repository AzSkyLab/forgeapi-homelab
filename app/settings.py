from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings. Every value has a default; nothing is required to start."""

    model_config = SettingsConfigDict(env_prefix="FORGEAPI_", env_file=".env", extra="ignore")

    data_dir: Path = Path(".local/data")
    catalog_path: Path = Path("patterns.yaml")
    terraform_bin: str = "terraform"
    version_cache_seconds: float = 60  # pattern tag cache for upgrade detection; 0 disables

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    task_queue: str = "forgeapi"
    # Opt-in TLS to a self-hosted Temporal server; no API keys involved. The cert/key pair is for
    # mTLS: both or neither. `temporal_tls_server_name` overrides SNI/authority (e.g. behind a
    # proxy or a cluster-internal name the certificate doesn't carry).
    temporal_tls: bool = False
    temporal_tls_ca_path: Path | None = None
    temporal_tls_cert_path: Path | None = None
    temporal_tls_key_path: Path | None = None
    temporal_tls_server_name: str | None = None

    # "none": loopback development only. "entra": validate bearer tokens (two values below).
    # "easyauth": trust the identity header Container Apps / App Service Easy Auth adds; only safe
    # when Easy Auth is in front, because it strips client-supplied copies of that header.
    auth_mode: Literal["none", "entra", "easyauth"] = "none"
    entra_tenant_id: str | None = None
    entra_audience: str | None = None
    # "none" mode trusts every caller as "local"; without this, a non-loopback caller is
    # refused rather than silently treated as local. Lab/demo deployments only.
    allow_unauthenticated_remote: bool = False

    # Business-unit mapping (docs/tenancy.md). Unset: single-tenant, no placement or ownership.
    tenants_path: Path | None = None
    # The same mapping as text, for hosts where the file cannot be baked into the image (it is
    # tenant-specific). Takes precedence over the path. Can come from a Key Vault reference.
    tenants_yaml: str | None = None
    dev_groups: str = ""  # comma-separated group IDs the caller has when auth_mode is "none"
    # Where the business-unit mapping lives. "file": tenants_path / tenants_yaml (default,
    # unchanged). "db": the `teams` table in the operation ledger's SQLite file, edited through
    # the operator-only /admin API. In "db" mode operators and auditors come ONLY from the two
    # settings below (comma-separated Entra group IDs), never from the database, so no API call
    # can grant anyone operator rights.
    tenants_source: Literal["file", "db"] = "file"
    operator_groups: str = ""
    auditor_groups: str = ""

    # A deployment that claims to be running but has not moved for this long is treated as
    # interrupted (its worker was stopped) and marked failed so it can be retried.
    stale_after_seconds: int = 300

    # Opt-in: the worker starts a sweep that runs a read-only drift check on every idle ready
    # resource not checked within this many minutes. Unset or 0: no sweep.
    drift_sweep_minutes: float | None = None

    # Opt-in: a `planned` operation older than this many hours is expired lazily, when someone
    # tries to execute it or submit a new intent for its resource. Unset: plans never expire.
    plan_max_age_hours: float | None = None

    # Where deployment records live. "table" is Azure Table Storage, for hosting.
    db_backend: Literal["sqlite", "table"] = "sqlite"
    table_storage_account: str | None = None
    table_name: str = "deployments"
    table_connection_string: str | None = None  # Azurite emulator only; real use is Entra auth

    # Read access to private pattern repos. Unset: git uses the host's own credential helper.
    # Local development only; a hosted deployment uses a GitHub App installation token.
    github_token: str | None = None
    # Better, when the organisation has one: a GitHub App. Tokens are minted per hour.
    github_app_id: str | None = None
    github_app_installation_id: str | None = None
    github_app_private_key: str | None = None  # PEM text, via a Key Vault secret reference
    github_host: str = "github.com"  # GitHub Enterprise Server: your host
    github_api_url: str = "https://api.github.com"  # GHES: https://<host>/api/v3

    # Identity Terraform runs as (handed over as ARM_* env). The certificate is for local
    # development only; leave it unset once hosted with a managed identity.
    azure_tenant_id: str | None = None
    azure_subscription_id: str | None = None
    azure_client_id: str | None = None
    azure_client_certificate_path: Path | None = None
    # Hosted on Azure: use the app's managed identity for Terraform (provider and state backend)
    # and for Table Storage. Takes precedence over the certificate.
    azure_use_managed_identity: bool = False
    # Client ID of a user-assigned managed identity, when the app has one.
    azure_managed_identity_client_id: str | None = None
    # Terraform cannot read a Container Apps managed identity itself (it only knows the VM
    # metadata address). Instead the worker fetches a managed-identity token and Terraform
    # presents it as a federated credential for this app registration, which holds the Azure
    # rights. Still no secret.
    azure_federated_client_id: str | None = None
    # On AKS with workload identity: the pod webhook injects AZURE_CLIENT_ID, AZURE_TENANT_ID and
    # AZURE_FEDERATED_TOKEN_FILE, and the azurerm provider and state backend read them when this
    # is set. Mutually exclusive with the federated client id, managed identity and client
    # certificate identity modes above.
    azure_use_aks_workload_identity: bool = False

    # Remote state for patterns that declare `backend "azurerm" {}`: one blob per deployment,
    # Entra auth only (no storage keys).
    state_resource_group: str | None = None
    state_storage_account: str | None = None
    state_container: str = "tfstate"

    @model_validator(mode="after")
    def _check_identity_combinations(self):
        if bool(self.temporal_tls_cert_path) != bool(self.temporal_tls_key_path):
            raise ValueError(
                "temporal_tls_cert_path and temporal_tls_key_path must both be set, or neither"
            )
        if self.azure_use_aks_workload_identity and (
            self.azure_federated_client_id
            or self.azure_use_managed_identity
            or self.azure_client_certificate_path
        ):
            raise ValueError(
                "azure_use_aks_workload_identity cannot be combined with the federated client "
                "id, managed identity or client certificate identity modes"
            )
        if self.plan_max_age_hours is not None and self.plan_max_age_hours <= 0:
            raise ValueError("plan_max_age_hours must be greater than 0 when set")
        if self.drift_sweep_minutes is not None and self.drift_sweep_minutes < 0:
            raise ValueError("drift_sweep_minutes must not be negative")
        return self


settings = Settings()
