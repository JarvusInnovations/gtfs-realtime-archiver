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

# Generic consumer-domain wiring.
# The module deliberately has no project-specific variables (bucket names,
# API-credential secrets, etc.); consumers express those through these maps.

variable "extra_env" {
  description = "Additional plain environment variables injected into every Dagster component (webserver, daemon, code servers, run workers). Use for consumer-domain config like bucket names or secret IDs the app resolves itself."
  type        = map(string)
  default     = {}

  validation {
    condition     = length(setintersection(keys(var.extra_env), ["GCP_PROJECT_ID", "GCP_REGION", "DAGSTER_HOME", "DAGSTER_LOGS_BUCKET"])) == 0
    error_message = "extra_env must not override the module-managed env vars GCP_PROJECT_ID, GCP_REGION, DAGSTER_HOME, or DAGSTER_LOGS_BUCKET."
  }
}

variable "bucket_grants" {
  description = <<-EOT
    GCS bucket IAM grants for the Dagster service accounts. Map key is a stable
    label (it becomes part of the Terraform resource key — renaming it moves
    state). Per entry:
      - bucket:          bucket name to grant on
      - dagster_role:    role for the primary SA (webserver/daemon/code servers),
                         or null for no grant
      - run_worker_role: role for each per-code-location run-worker SA, or null
  EOT
  type = map(object({
    bucket          = string
    dagster_role    = optional(string)
    run_worker_role = optional(string)
  }))
  default = {}
}

variable "secret_grants" {
  description = <<-EOT
    Secret Manager accessor grants for the Dagster service accounts. Map key is a
    stable label (part of the Terraform resource key). Per entry:
      - secret_id:  Secret Manager secret ID
      - dagster:    grant to the primary SA (default false)
      - run_worker: grant to each run-worker SA (default true)
  EOT
  type = map(object({
    secret_id  = string
    dagster    = optional(bool, false)
    run_worker = optional(bool, true)
  }))
  default = {}
}

variable "run_worker_secret_env" {
  description = "Environment variables injected into run workers as Secret Manager references: env var name -> secret ID. The secret must also be granted via secret_grants (run_worker = true)."
  type        = map(string)
  default     = {}
}

variable "enable_dbt_hmac_keys" {
  description = "Issue a GCS HMAC key per run-worker SA and expose it to run workers via Secret Manager-backed env vars (see hmac.tf). For DuckDB/dbt-duckdb httpfs writes to gs:// via the S3-compatible API."
  type        = bool
  default     = false
}

variable "hmac_env_names" {
  description = "Env var names the run worker receives the HMAC credentials under (only used when enable_dbt_hmac_keys = true). Match what the consumer's profiles.yml/env_var() reads."
  type = object({
    key_id = optional(string, "DAGSTER_GCS_HMAC_KEY_ID")
    secret = optional(string, "DAGSTER_GCS_HMAC_SECRET")
  })
  default = {}
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
# supported Cloud Run CPU size. Defaults sum to 1 vCPU / 2.5Gi.
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
    daemon      = { cpu = "250m", memory = "1Gi" } # matches split's daemon memory; 512Mi risks OOM loops
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

# Web exposure mode
# Three patterns are supported:
# 1. Public + IAP    — set iap_allowed_domain (+ custom_domain); webserver gets a
#    public ingress with Google-managed IAP gating the @domain login.
# 2. Public          — iap_allowed_domain null, public_ingress true: an allUsers
#    invoker binding is created. Unauthenticated; opt-in only.
# 3. Private + proxy — iap_allowed_domain null, public_ingress false; no invoker
#    binding is created and callers invoke through Cloud Run IAM (e.g. another
#    Cloud Run service forwarding requests with an ID token). Pair with
#    path_prefix when the proxy mounts the UI under a subpath.
variable "iap_allowed_domain" {
  description = "Google Workspace domain for IAP access. Null disables IAP (see public_ingress for the non-IAP postures)."
  type        = string
  default     = null
}

variable "custom_domain" {
  description = "Custom domain for the webserver (requires DNS record). Creates a Cloud Run domain mapping whenever set, independent of IAP; pair with an ingress posture that makes the domain reachable."
  type        = string
  default     = null
}

variable "project_number" {
  description = "GCP project number (required for IAP service account)"
  type        = string
  default     = null
}

variable "public_ingress" {
  description = "If true and IAP is disabled, an allUsers run.invoker binding exposes the webserver publicly. If false, access happens via run.invoker IAM granted (outside the module) to a specific caller SA."
  type        = bool
  default     = true
}

variable "path_prefix" {
  description = "URL path prefix the webserver serves under (passed to dagster-webserver --path-prefix). Empty string means served at root. Set to e.g. \"/dagster\" when a reverse proxy forwards a subpath."
  type        = string
  default     = ""

  validation {
    # Interpolated into Cloud Run probe paths (must start with "/") and passed
    # to --path-prefix; a trailing slash would yield "//server_info".
    condition     = var.path_prefix == "" || (startswith(var.path_prefix, "/") && !endswith(var.path_prefix, "/"))
    error_message = "path_prefix must be empty, or start with \"/\" and not end with \"/\" (e.g. \"/dagster\")."
  }
}

variable "webserver_min_instances" {
  description = "Minimum webserver instances in split mode. 0 scales to zero (cold start on first UI hit); 1 keeps the UI warm for a small always-on memory cost."
  type        = number
  default     = 0
}

variable "code_server_min_instances" {
  description = "Minimum code-server instances in split mode. The always-on daemon keeps the code server warm in practice, so 0 is usually fine; 1 makes the warmth explicit and removes sensor-tick cold starts."
  type        = number
  default     = 0
}
