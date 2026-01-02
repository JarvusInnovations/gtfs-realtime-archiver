variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "container_image" {
  description = "Container image URL for the archiver"
  type        = string
  default     = "us-central1-docker.pkg.dev/gtfs-archiver/gtfs-rt-archiver/gtfs-rt-archiver:latest"
}

variable "service_name" {
  description = "Name of the Cloud Run service"
  type        = string
  default     = "gtfs-rt-archiver"
}

variable "bucket_name" {
  description = "Name of the GCS bucket for archived feeds"
  type        = string
}

variable "bucket_location" {
  description = "Location of the GCS bucket"
  type        = string
  default     = "us-central1"
}

variable "gcs_prefix" {
  description = "Path prefix within the GCS bucket"
  type        = string
  default     = ""
}

variable "max_concurrent" {
  description = "Maximum concurrent feed fetches"
  type        = number
  default     = 100
}

variable "cpu" {
  description = "CPU allocation for Cloud Run service"
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory allocation for Cloud Run service"
  type        = string
  default     = "1Gi"
}

variable "min_instances" {
  description = "Minimum number of instances (0 for scale to zero)"
  type        = number
  default     = 1
}

variable "max_instances" {
  description = "Maximum number of instances"
  type        = number
  default     = 1
}

variable "log_level" {
  description = "Logging level (DEBUG, INFO, WARNING, ERROR)"
  type        = string
  default     = "INFO"
}

variable "secret_env_vars" {
  description = "Map of environment variable names to Secret Manager secret IDs"
  type        = map(string)
  default     = {}
}
