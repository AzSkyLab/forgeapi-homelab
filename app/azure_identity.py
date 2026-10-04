"""Which Azure identity this process runs as. Hosted: the app's managed identity. Local: the
approved short-lived certificate. Never a client secret or a storage key."""

import json
import urllib.error
import urllib.request
from urllib.parse import quote

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


def member_groups(object_id: str) -> set[str] | None:
    """Group IDs the user or service principal currently belongs to (transitively); None if the
    object no longer exists. Graph returns them all in one response (up to 11000): no paging."""
    token = credential().get_token("https://graph.microsoft.com/.default").token
    url = f"https://graph.microsoft.com/v1.0/directoryObjects/{quote(object_id, safe='')}"
    request = urllib.request.Request(
        f"{url}/getMemberGroups",
        data=json.dumps({"securityEnabledOnly": False}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return set(json.load(response)["value"])
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
