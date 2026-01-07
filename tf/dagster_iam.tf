# Project-specific IAM grants for Dagster service accounts
# Extends the core Dagster permissions defined in tf/modules/dagster/iam.tf
#
# The module handles Dagster-internal permissions:
# - Logs bucket access (module creates/manages)
# - Secret Manager (DB password, config secrets)
# - Cloud SQL client role
# - Cloud Run admin (for launching jobs)
#
# This file handles project-specific resource access:
# - Protobuf and parquet buckets (created outside module)
# - Any other project-specific resources

# Run workers - protobuf bucket read
resource "google_storage_bucket_iam_member" "run_worker_protobuf_read" {
  for_each = module.dagster.run_worker_service_account_emails

  bucket = google_storage_bucket.protobuf.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${each.value}"
}

# Run workers - parquet bucket write (objectAdmin needed for overwrites)
resource "google_storage_bucket_iam_member" "run_worker_parquet_write" {
  for_each = module.dagster.run_worker_service_account_emails

  bucket = google_storage_bucket.parquet.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${each.value}"
}

# Add future project-specific grants below...
# Examples:
# - BigQuery dataset access
# - Additional GCS buckets
# - Pub/Sub topics
# - Custom APIs
