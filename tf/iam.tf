resource "google_service_account" "archiver" {
  account_id   = "${var.service_name}-sa"
  display_name = "GTFS-RT Archiver Service Account"
  project      = var.project_id
}

# Archiver writes protobuf snapshots
resource "google_storage_bucket_iam_member" "archiver_protobuf" {
  bucket = google_storage_bucket.protobuf.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.archiver.email}"
}

# Dagster reads protobuf and writes parquet (using same service account)
resource "google_storage_bucket_iam_member" "archiver_parquet_read" {
  bucket = google_storage_bucket.protobuf.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.archiver.email}"
}

resource "google_storage_bucket_iam_member" "archiver_parquet_write" {
  bucket = google_storage_bucket.parquet.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.archiver.email}"
}

# Allow Cloud Run to use the service account
resource "google_project_iam_member" "archiver_run" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.archiver.email}"
}

# Grant access to secrets with type=feed-key tag
# Secrets must be tagged with type=feed-key to be accessible
resource "google_project_iam_member" "archiver_feed_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.archiver.email}"

  condition {
    title       = "feed-key-secrets"
    description = "Access to secrets tagged type=feed-key"
    expression  = "resource.matchTag('${var.project_id}/type', 'feed-key')"
  }
}

# Grant access to agencies config secret
resource "google_secret_manager_secret_iam_member" "archiver_agencies_config" {
  project   = var.project_id
  secret_id = var.agencies_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.archiver.email}"
}

# Allow sidecar to write Prometheus metrics to Cloud Monitoring
resource "google_project_iam_member" "archiver_metrics_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.archiver.email}"
}
