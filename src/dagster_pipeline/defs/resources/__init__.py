"""Resources for the Dagster pipeline."""

from dagster_pipeline.defs.resources.gcs import GCSResource
from dagster_pipeline.defs.resources.secret_manager import SecretManagerResource

__all__ = ["GCSResource", "SecretManagerResource"]
