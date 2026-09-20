from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings. Every value has a default; nothing is required to start."""

    model_config = SettingsConfigDict(env_prefix="FORGEAPI_", env_file=".env", extra="ignore")

    data_dir: Path = Path(".local/data")
    catalog_path: Path = Path("patterns.yaml")
    terraform_bin: str = "terraform"

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    task_queue: str = "forgeapi"

    # "none" is for loopback development only. "entra" requires the two values below.
    auth_mode: Literal["none", "entra"] = "none"
    entra_tenant_id: str | None = None
    entra_audience: str | None = None

    # Where deployment records live. "table" is Azure Table Storage, for hosting.
    db_backend: Literal["sqlite", "table"] = "sqlite"
    table_storage_account: str | None = None
    table_name: str = "deployments"
    table_connection_string: str | None = None  # Azurite emulator only; real use is Entra auth

    # Read access to private pattern repos. Unset: git uses the host's own credential helper.
    # Local development only; a hosted deployment uses a GitHub App installation token.
    github_token: str | None = None

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

    # Remote state for patterns that declare `backend "azurerm" {}`: one blob per deployment,
    # Entra auth only (no storage keys).
    state_resource_group: str | None = None
    state_storage_account: str | None = None
    state_container: str = "tfstate"


settings = Settings()
