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
  description = "Container image URL for the archiver (via Artifact Registry remote repo)"
  type        = string
  # Note: GHCR normalizes repository names to lowercase
  default     = "us-central1-docker.pkg.dev/gtfs-archiver/ghcr-remote/jarvusinnovations/gtfs-realtime-archiver:latest"
}

variable "service_name" {
  description = "Name of the Cloud Run service"
  type        = string
  default     = "gtfs-rt-archiver"
}

variable "protobuf_bucket_name" {
  description = "Name of the GCS bucket for raw protobuf archives"
  type        = string
  default     = "protobuf.gtfsrt.io"
}

variable "parquet_bucket_name" {
  description = "Name of the GCS bucket for compacted parquet files"
  type        = string
  default     = "parquet.gtfsrt.io"
}

variable "bucket_location" {
  description = "Location of the GCS buckets"
  type        = string
  default     = "us-central1"
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

variable "github_org" {
  description = "GitHub organization for Workload Identity Federation"
  type        = string
  default     = "JarvusInnovations"
}

variable "github_repo" {
  description = "GitHub repository for Workload Identity Federation"
  type        = string
  default     = "gtfs-realtime-archiver"
}

variable "agencies_secret_id" {
  description = "Secret Manager secret ID containing agencies.yaml configuration"
  type        = string
  default     = "agencies-config"
}

variable "dns_zone_name" {
  description = "Name of the externally-managed Cloud DNS zone for gtfsrt.io"
  type        = string
  default     = "gtfsrt-io"
}
