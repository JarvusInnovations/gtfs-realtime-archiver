"""Google Cloud Secret Manager resource for Dagster."""

import dagster as dg
from google.cloud import secretmanager
from pydantic import Field


class SecretManagerResource(dg.ConfigurableResource):  # type: ignore[type-arg]
    """Google Cloud Secret Manager client resource.

    Provides access to secrets stored in Secret Manager.
    """

    project_id: str = Field(description="GCP project ID for Secret Manager")
    agencies_secret_id: str = Field(
        default="agencies-config",
        description="Secret ID containing agencies.yaml configuration",
    )

    def get_client(self) -> secretmanager.SecretManagerServiceClient:
        """Get a Secret Manager client instance."""
        return secretmanager.SecretManagerServiceClient()

    def get_secret(self, secret_id: str | None = None) -> str:
        """Fetch secret value from Secret Manager.

        Args:
            secret_id: Secret ID to fetch. Defaults to agencies_secret_id.

        Returns:
            The secret value as a string.
        """
        client = self.get_client()
        name = client.secret_version_path(
            self.project_id,
            secret_id or self.agencies_secret_id,
            "latest",
        )
        response = client.access_secret_version(name=name)
        return response.payload.data.decode("utf-8")
