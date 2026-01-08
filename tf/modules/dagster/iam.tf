# Primary Dagster service account (webserver, daemon, code server)
resource "google_service_account" "dagster" {
  account_id   = "dagster"
  display_name = "Dagster Service Account"
  description  = "Service account for Dagster webserver, daemon, and code servers"
  project      = var.project_id
}

# Per-code-location run worker service accounts
resource "google_service_account" "run_worker" {
  for_each = var.code_locations

  account_id   = "dagster-rw-${each.key}"
  display_name = "Dagster Run Worker - ${each.key}"
  description  = "Service account for Dagster run workers in ${each.key} code location"
  project      = var.project_id
}

# Cloud Run admin for launching run jobs
resource "google_project_iam_member" "dagster_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.dagster.email}"
}

# Allow primary SA to act as run worker SAs when launching jobs
resource "google_service_account_iam_member" "dagster_can_impersonate_run_workers" {
  for_each = google_service_account.run_worker

  service_account_id = each.value.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.dagster.email}"
}

# Secret Manager access for primary SA (postgres URL with connection string)
resource "google_secret_manager_secret_iam_member" "dagster_postgres_url" {
  secret_id = google_secret_manager_secret.postgres_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dagster.email}"
  project   = var.project_id
}

# Secret Manager access for run workers (postgres URL with connection string)
resource "google_secret_manager_secret_iam_member" "run_worker_postgres_url" {
  for_each = google_service_account.run_worker

  secret_id = google_secret_manager_secret.postgres_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value.email}"
  project   = var.project_id
}

# GCS access for primary SA (logs bucket)
resource "google_storage_bucket_iam_member" "dagster_logs" {
  bucket = local.logs_bucket_name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.dagster.email}"
}

# Logs bucket metadata access for primary SA (GCSComputeLogManager needs storage.buckets.get)
resource "google_storage_bucket_iam_member" "dagster_logs_reader" {
  bucket = local.logs_bucket_name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.dagster.email}"
}

# Logs bucket access for run workers
resource "google_storage_bucket_iam_member" "run_worker_logs" {
  for_each = google_service_account.run_worker

  bucket = local.logs_bucket_name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${each.value.email}"
}

# Logs bucket metadata access for run workers (GCSComputeLogManager needs storage.buckets.get)
resource "google_storage_bucket_iam_member" "run_worker_logs_reader" {
  for_each = google_service_account.run_worker

  bucket = local.logs_bucket_name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${each.value.email}"
}

# Cloud SQL client role for service accounts (required for socket connections)
resource "google_project_iam_member" "dagster_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.dagster.email}"
}

resource "google_project_iam_member" "run_worker_cloudsql_client" {
  for_each = google_service_account.run_worker

  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${each.value.email}"
}
