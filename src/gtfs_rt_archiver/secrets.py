"""GCP Secret Manager client for fetching authentication secrets."""

import asyncio
from typing import TYPE_CHECKING

from google.cloud import secretmanager_v1

if TYPE_CHECKING:
    from gtfs_rt_archiver.models import AuthConfig

# Module-level cache for resolved secrets
_secret_cache: dict[str, str] = {}
_cache_lock = asyncio.Lock()


class SecretManagerError(Exception):
    """Error fetching secret from Secret Manager."""

    def __init__(self, secret_name: str, message: str) -> None:
        self.secret_name = secret_name
        super().__init__(f"Failed to fetch secret '{secret_name}': {message}")


async def get_secret(project_id: str, secret_name: str) -> str:
    """Fetch a secret value from GCP Secret Manager.

    Args:
        project_id: GCP project ID.
        secret_name: Name of the secret in Secret Manager.

    Returns:
        The secret value as a string.

    Raises:
        SecretManagerError: If the secret cannot be fetched.
    """
    cache_key = f"{project_id}/{secret_name}"

    # Check cache first
    async with _cache_lock:
        if cache_key in _secret_cache:
            return _secret_cache[cache_key]

    try:
        client = secretmanager_v1.SecretManagerServiceAsyncClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"

        response = await client.access_secret_version(
            request=secretmanager_v1.AccessSecretVersionRequest(name=name)
        )

        secret_value = response.payload.data.decode("utf-8")

        # Cache the result
        async with _cache_lock:
            _secret_cache[cache_key] = secret_value

        return secret_value

    except Exception as e:
        raise SecretManagerError(secret_name, str(e)) from e


async def resolve_auth_config(
    auth: "AuthConfig",
    project_id: str,
) -> None:
    """Resolve the secret value for an AuthConfig.

    Fetches the secret from Secret Manager and populates the resolved_value field.

    Args:
        auth: AuthConfig to resolve.
        project_id: GCP project ID for Secret Manager.

    Raises:
        SecretManagerError: If the secret cannot be fetched.
    """
    secret_value = await get_secret(project_id, auth.secret_name)
    if auth.value is None:
        # No value specified - use entire secret directly
        auth.resolved_value = secret_value
    else:
        # Value specified - perform template substitution
        auth.resolved_value = auth.value.replace("${SECRET}", secret_value)


def clear_cache() -> None:
    """Clear the secret cache. Useful for testing."""
    _secret_cache.clear()
