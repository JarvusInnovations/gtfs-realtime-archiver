# Agent/operator programmatic access to the IAP-gated Dagster webserver.
#
# User credentials cannot mint the audience-bound tokens Cloud Run IAP requires,
# so agents (Claude sessions, scripts) impersonate this dedicated service
# account, which can ONLY pass IAP — it holds no data or infra roles.
#
# This IAP uses a Google-managed OAuth client, which does NOT support the
# impersonated-ID-token flow: `gcloud auth print-identity-token
# --impersonate-service-account=... --audiences=...` fails with "Invalid JWT
# audience" for every audience value. Use a self-signed JWT instead, signed via
# iamcredentials so no key file is needed, with the audience carrying a path
# wildcard:
#
#   aud = "https://dagster.gtfsrt.io/*"   (the trailing /* is required)
#   POST .../serviceAccounts/agent-graphql@...:signJwt
#   Authorization: Bearer <signedJwt>
#
# The `dagster-api` skill (.claude/skills/dagster-api/) implements this and is
# the supported way in; see its SKILL.md for the full rationale.

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
