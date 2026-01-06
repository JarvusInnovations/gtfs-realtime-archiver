output "service_url" {
  description = "URL of the Cloud Run service"
  value       = google_cloud_run_v2_service.archiver.uri
}

output "service_name" {
  description = "Name of the Cloud Run service"
  value       = google_cloud_run_v2_service.archiver.name
}

output "protobuf_bucket_name" {
  description = "Name of the GCS protobuf bucket"
  value       = google_storage_bucket.protobuf.name
}

output "protobuf_bucket_url" {
  description = "URL of the GCS protobuf bucket"
  value       = google_storage_bucket.protobuf.url
}

output "parquet_bucket_name" {
  description = "Name of the GCS parquet bucket"
  value       = google_storage_bucket.parquet.name
}

output "parquet_bucket_url" {
  description = "URL of the GCS parquet bucket"
  value       = google_storage_bucket.parquet.url
}

output "service_account_email" {
  description = "Email of the service account"
  value       = google_service_account.archiver.email
}

output "workload_identity_provider" {
  description = "Workload Identity Provider resource name for GitHub Actions"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_actions_service_account" {
  description = "Service account email for GitHub Actions deployments"
  value       = google_service_account.github_actions.email
}

output "dagster_service_account_email" {
  description = "Email of the Dagster pipeline service account"
  value       = google_service_account.dagster.email
}

# Cloud SQL outputs
output "cloudsql_instance_name" {
  description = "Name of the Cloud SQL instance"
  value       = google_sql_database_instance.dagster.name
}

output "cloudsql_connection_name" {
  description = "Connection name for Cloud SQL (project:region:instance)"
  value       = google_sql_database_instance.dagster.connection_name
}

# Dagster module outputs
output "dagster_webserver_url" {
  description = "URL of the Dagster webserver"
  value       = module.dagster.webserver_url
}

output "dagster_code_server_urls" {
  description = "URLs of Dagster code servers by location"
  value       = module.dagster.code_server_urls
}

output "dagster_run_worker_job_names" {
  description = "Cloud Run job names for run workers by location"
  value       = module.dagster.run_worker_job_names
}
