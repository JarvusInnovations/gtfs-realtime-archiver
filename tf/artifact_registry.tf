# Artifact Registry remote repository for GitHub Container Registry
#
# Cloud Run cannot pull images directly from GHCR, so we create a remote
# repository that acts as a pull-through cache/proxy for GHCR images.

resource "google_artifact_registry_repository" "ghcr" {
  repository_id = "ghcr-remote"
  location      = var.region
  project       = var.project_id
  description   = "Remote repository for GitHub Container Registry"
  format        = "DOCKER"
  mode          = "REMOTE_REPOSITORY"

  remote_repository_config {
    description = "GitHub Container Registry"

    docker_repository {
      custom_repository {
        uri = "https://ghcr.io"
      }
    }
  }
}

# Grant the archiver service account permission to pull from the remote repository
resource "google_artifact_registry_repository_iam_member" "archiver_reader" {
  repository = google_artifact_registry_repository.ghcr.name
  location   = google_artifact_registry_repository.ghcr.location
  project    = var.project_id
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.archiver.email}"
}
