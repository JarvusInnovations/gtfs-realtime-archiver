output "service_url" {
  description = "URL of the Cloud Run service"
  value       = google_cloud_run_v2_service.archiver.uri
}

output "service_name" {
  description = "Name of the Cloud Run service"
  value       = google_cloud_run_v2_service.archiver.name
}

output "bucket_name" {
  description = "Name of the GCS archive bucket"
  value       = google_storage_bucket.archive.name
}

output "bucket_url" {
  description = "URL of the GCS archive bucket"
  value       = google_storage_bucket.archive.url
}

output "service_account_email" {
  description = "Email of the service account"
  value       = google_service_account.archiver.email
}
