"""Google Cloud Storage resource for Dagster."""

from google.cloud import storage
from pydantic import Field

import dagster as dg


class GCSResource(dg.ConfigurableResource):  # type: ignore[type-arg]
    """Google Cloud Storage client resource.

    Provides a GCS client configured with the specified project ID.
    If project_id is not specified, uses Application Default Credentials.
    """

    project_id: str | None = None
    source_bucket: str = Field(description="Bucket containing raw protobuf archives")
    output_bucket: str = Field(description="Bucket for compacted parquet output")

    def get_client(self) -> storage.Client:
        """Get a GCS client instance."""
        return storage.Client(project=self.project_id)
