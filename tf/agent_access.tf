# Agent/operator programmatic access to the IAP-gated Dagster webserver.
#
# User credentials cannot mint the audience-bound ID tokens Cloud Run IAP
# requires, so agents (Claude sessions, scripts) impersonate this dedicated
# service account, which can ONLY pass IAP — it holds no data or infra roles.
#
#   gcloud auth print-identity-token \
#     --impersonate-service-account=agent-graphql@gtfs-archiver.iam.gserviceaccount.com \
#     --audiences=<webserver URL>

resource "google_service_account" "agent_graphql" {
  account_id   = "agent-graphql"
  display_name = "Agent GraphQL access (IAP)"
  description  = "Impersonation target for programmatic access through the Dagster webserver's IAP; no other permissions."
  project      = var.project_id
}

# Created imperatively 2026-07-10 during the run-launching incident; adopt it.
# Delete this block after the first successful apply.
import {
  to = google_service_account.agent_graphql
  id = "projects/gtfs-archiver/serviceAccounts/agent-graphql@gtfs-archiver.iam.gserviceaccount.com"
}

resource "google_iap_web_cloud_run_service_iam_member" "agent_graphql_accessor" {
  provider               = google-beta
  project                = data.google_project.current.number
  location               = var.region
  cloud_run_service_name = "dagster-webserver"
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "serviceAccount:${google_service_account.agent_graphql.email}"
}

resource "google_service_account_iam_member" "chris_impersonates_agent_graphql" {
  service_account_id = google_service_account.agent_graphql.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "user:chris@jarv.us"
}
