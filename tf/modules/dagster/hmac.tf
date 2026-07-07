# Optional GCS HMAC credentials per run-worker SA (var.enable_dbt_hmac_keys).
#
# Why: pipelines that use DuckDB/dbt-duckdb with `materialized = 'external'`
# write models directly to gs://... locations so separate run-worker containers
# can resolve `ref()` across runs. Those writes go through DuckDB's httpfs
# extension, which speaks S3-compatible auth — an HMAC key provides that for
# GCS.
#
# Lifecycle is managed by Terraform: rotation is
# `tofu taint 'module.<name>.google_storage_hmac_key.dbt_gcs["<location>"]'`
# + apply. Values flow into Secret Manager and into the run-worker job template
# via env var refs (see run_worker.tf; names from var.hmac_env_names). HMAC
# values exist in Terraform state — same trust boundary as the postgres URL and
# other auto-generated secrets.
#
# One key per code location, mirroring the per-code-location run-worker SA
# pattern; future code locations get their own key automatically.

resource "google_storage_hmac_key" "dbt_gcs" {
  for_each = var.enable_dbt_hmac_keys ? google_service_account.run_worker : {}

  service_account_email = each.value.email
  project               = var.project_id
}

resource "google_secret_manager_secret" "dbt_gcs_hmac_key_id" {
  for_each  = google_storage_hmac_key.dbt_gcs
  secret_id = "dbt-gcs-hmac-key-id-${each.key}"
  project   = var.project_id

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "dbt_gcs_hmac_secret" {
  for_each  = google_storage_hmac_key.dbt_gcs
  secret_id = "dbt-gcs-hmac-secret-${each.key}"
  project   = var.project_id

  replication {
    auto {}
  }
}

# No ignore_changes — when the HMAC key is rotated (tofu taint + apply),
# a new secret version is written and Cloud Run picks it up on next job
# launch via `version = "latest"`.
resource "google_secret_manager_secret_version" "dbt_gcs_hmac_key_id" {
  for_each    = google_storage_hmac_key.dbt_gcs
  secret      = google_secret_manager_secret.dbt_gcs_hmac_key_id[each.key].id
  secret_data = each.value.access_id
}

resource "google_secret_manager_secret_version" "dbt_gcs_hmac_secret" {
  for_each    = google_storage_hmac_key.dbt_gcs
  secret      = google_secret_manager_secret.dbt_gcs_hmac_secret[each.key].id
  secret_data = each.value.secret
}

# Each run-worker SA can read its own HMAC credentials at job start.
resource "google_secret_manager_secret_iam_member" "run_worker_hmac_key_id" {
  for_each = google_storage_hmac_key.dbt_gcs

  project   = var.project_id
  secret_id = google_secret_manager_secret.dbt_gcs_hmac_key_id[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run_worker[each.key].email}"
}

resource "google_secret_manager_secret_iam_member" "run_worker_hmac_secret" {
  for_each = google_storage_hmac_key.dbt_gcs

  project   = var.project_id
  secret_id = google_secret_manager_secret.dbt_gcs_hmac_secret[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run_worker[each.key].email}"
}
