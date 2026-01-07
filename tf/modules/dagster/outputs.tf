# Webserver outputs
output "webserver_url" {
  description = "URL of the Dagster webserver"
  value       = google_cloud_run_v2_service.webserver.uri
}

output "webserver_service_name" {
  description = "Name of the webserver Cloud Run service"
  value       = google_cloud_run_v2_service.webserver.name
}

# Service account outputs
output "dagster_service_account_email" {
  description = "Email of the primary Dagster service account"
  value       = google_service_account.dagster.email
}

output "run_worker_service_account_emails" {
  description = "Map of code location names to run worker service account emails"
  value       = { for k, v in google_service_account.run_worker : k => v.email }
}

# Code server outputs
output "code_server_urls" {
  description = "Map of code location names to code server URLs"
  value       = { for k, v in google_cloud_run_v2_service.code_server : k => v.uri }
}

# Run worker job outputs
output "run_worker_job_names" {
  description = "Map of code location names to run worker Cloud Run job names"
  value       = { for k, v in google_cloud_run_v2_job.run_worker : k => v.name }
}

# Database outputs
output "database_name" {
  description = "Name of the Dagster database"
  value       = google_sql_database.dagster.name
}

# Logs bucket output
output "logs_bucket_name" {
  description = "Name of the GCS bucket for Dagster compute logs"
  value       = local.logs_bucket_name
}
