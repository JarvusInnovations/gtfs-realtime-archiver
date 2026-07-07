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

# Deployment topology
variable "deployment_mode" {
  description = <<-EOT
    Dagster topology:
      - "split"        (default): webserver, daemon, and code server each run as
                       their own Cloud Run resource. Webserver scales 0->N, daemon
                       is a single-instance Worker Pool, code server is isolated.
                       Use when you need horizontal UI scaling or multiple code
                       locations.
      - "consolidated": webserver (ingress) + daemon + code server run as three
                       containers in ONE always-on Cloud Run Service instance.
                       Lowest cost floor; single code location only. The instance
                       is pinned to exactly 1 (daemon must be a singleton) and uses
                       instance-based billing (always-allocated CPU) so the daemon
                       sidecar is not starved between UI requests.
  EOT
  type        = string
  default     = "split"

  validation {
    condition     = contains(["split", "consolidated"], var.deployment_mode)
    error_message = "deployment_mode must be either \"split\" or \"consolidated\"."
  }
}

# Per-container resource limits for the consolidated deployment.
# The instance total is the SUM across the three containers and must resolve to a
# supported Cloud Run CPU size. Defaults sum to 1 vCPU / 2Gi.
#
# Cost break-even (us-central1, always-allocated, no CUD): a consolidated instance
# runs ~$55/mo at the 1 vCPU default but ~$105-110/mo at 2 vCPU / 2.5Gi — the
# latter is a wash against a loaded split deployment's idle floor (~$100/mo:
# always-on daemon + daemon-kept-warm code server). Consolidation only *saves*
# money at roughly <=1.5 vCPU total; above that it's a topology simplification,
# not a cost cut. Size up only if the UI or code server is actually starved.
# If Cloud Run rejects the fractional per-container split at apply time, fall
# back to whole-CPU containers (1000m each, 3 vCPU total).
variable "consolidated_resources" {
  description = "Per-container resource limits for deployment_mode = consolidated. Instance cost scales with the SUM across containers; see the break-even note above this variable."
  type = object({
    webserver   = object({ cpu = string, memory = string })
    daemon      = object({ cpu = string, memory = string })
    code_server = object({ cpu = string, memory = string })
  })
  default = {
    webserver   = { cpu = "500m", memory = "512Mi" }
    code_server = { cpu = "250m", memory = "1Gi" }
    daemon      = { cpu = "250m", memory = "512Mi" }
  }
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

# IAP configuration
variable "iap_allowed_domain" {
  description = "Google Workspace domain for IAP access. Null disables IAP."
  type        = string
  default     = null
}

variable "custom_domain" {
  description = "Custom domain for webserver (requires DNS record)"
  type        = string
  default     = null
}

variable "project_number" {
  description = "GCP project number (required for IAP service account)"
  type        = string
  default     = null
}

variable "agencies_secret_id" {
  description = "Secret Manager secret ID containing agencies.yaml configuration"
  type        = string
  default     = "agencies-config"
}
