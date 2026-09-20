"""Which Azure identity this process runs as. Hosted: the app's managed identity. Local: the
approved short-lived certificate. Never a client secret or a storage key."""

from azure.core.credentials import TokenCredential
from azure.identity import CertificateCredential, DefaultAzureCredential, ManagedIdentityCredential

from app.settings import settings


def credential() -> TokenCredential:
    if settings.azure_use_managed_identity:
        return ManagedIdentityCredential(client_id=settings.azure_managed_identity_client_id)
    if settings.azure_client_certificate_path:
        return CertificateCredential(
            tenant_id=settings.azure_tenant_id,
            client_id=settings.azure_client_id,
            certificate_path=str(settings.azure_client_certificate_path),
        )
    return DefaultAzureCredential()


def federation_token() -> str:
    """Managed-identity token that Entra accepts as a federated credential (workload identity)."""
    return credential().get_token("api://AzureADTokenExchange/.default").token
