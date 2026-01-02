resource "google_service_account" "archiver" {
  account_id   = "${var.service_name}-sa"
  display_name = "GTFS-RT Archiver Service Account"
  project      = var.project_id
}

resource "google_storage_bucket_iam_member" "archiver_storage" {
  bucket = google_storage_bucket.archive.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.archiver.email}"
}

# Allow Cloud Run to use the service account
resource "google_project_iam_member" "archiver_run" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.archiver.email}"
}

# Grant access to secrets (if any configured)
resource "google_secret_manager_secret_iam_member" "archiver_secrets" {
  for_each = var.secret_env_vars

  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.archiver.email}"
}

# Grant access to feeds config secret
resource "google_secret_manager_secret_iam_member" "archiver_feeds_config" {
  project   = var.project_id
  secret_id = var.feeds_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.archiver.email}"
}
