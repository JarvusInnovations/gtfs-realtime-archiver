# Required variables
variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
}

variable "cloud_sql_connection_name" {
  description = "Cloud SQL instance connection name (project:region:instance)"
  type        = string
}

variable "protobuf_bucket_name" {
  description = "GCS bucket name for raw protobuf files"
  type        = string
}

variable "parquet_bucket_name" {
  description = "GCS bucket name for compacted parquet files"
  type        = string
}

# Container images
variable "webserver_image" {
  description = "Container image URL for Dagster webserver"
  type        = string
}

variable "daemon_image" {
  description = "Container image URL for Dagster daemon"
  type        = string
}

# Code locations configuration
variable "code_locations" {
  description = "Map of code location configurations"
  type = map(object({
    image             = string
    module_name       = string
    port              = number
    run_worker_cpu    = string
    run_worker_memory = string
  }))
}

# Optional variables with defaults
variable "db_name" {
  description = "Database name for Dagster"
  type        = string
  default     = "dagster"
}

variable "db_user" {
  description = "Database user for Dagster"
  type        = string
  default     = "dagster"
}

variable "webserver_resources" {
  description = "Resource limits for webserver"
  type = object({
    cpu    = string
    memory = string
  })
  default = {
    cpu    = "1"
    memory = "2Gi"
  }
}

variable "daemon_resources" {
  description = "Resource limits for daemon"
  type = object({
    cpu    = string
    memory = string
  })
  default = {
    cpu    = "1"
    memory = "1Gi"
  }
}

variable "code_server_resources" {
  description = "Resource limits for code server"
  type = object({
    cpu    = string
    memory = string
  })
  default = {
    cpu    = "1"
    memory = "1Gi"
  }
}

variable "run_timeout_seconds" {
  description = "Timeout for run worker jobs in seconds"
  type        = number
  default     = 86400 # 24 hours
}

variable "logs_bucket_name" {
  description = "GCS bucket name for Dagster compute logs (creates one if not provided)"
  type        = string
  default     = null
}

variable "labels" {
  description = "Labels to apply to all resources"
  type        = map(string)
  default     = {}
}
