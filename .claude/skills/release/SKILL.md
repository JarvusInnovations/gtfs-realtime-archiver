---
name: release
description: Cut a release for the gtfs-realtime-archiver. Use this when the user says "release", "deploy", "cut a release", "ship it", "push to production", or wants to get changes from develop into production. Also use when asked to update release PR notes or check release PR status.
---

# Release Process

The gtfs-realtime-archiver uses an automated develop → main release PR workflow. Never push directly to main.

## Flow

1. All work goes to `develop` branch
2. An automated GitHub Action creates/updates a draft release PR (develop → main) with an auto-incremented version
3. The PR has a comment with a changelog of all commits since last release
4. Before merging, update the PR description with release notes and optionally bump the version in the PR title
5. Merge the PR
6. Create a GitHub release tag — this triggers the deploy workflow

## Step-by-step

### 1. Check the release PR

```bash
gh pr list --repo JarvusInnovations/gtfs-realtime-archiver --state open
```

There should be one open PR titled "Release: v{X.Y.Z}" from develop → main.

### 2. Review the changelog

The PR has a comment with all commits pending for this release:

```bash
gh api repos/JarvusInnovations/gtfs-realtime-archiver/issues/{PR_NUMBER}/comments --jq '.[].body'
```

### 3. Write release notes

Split the changelog entries into two sections:

- **Improvements** — user-facing changes. Users are downstream data consumers and Dagster admins. Things like new data endpoints, new schedule URLs, new BigQuery tables, inventory changes.
- **Technical** — internal/backoffice changes. CI fixes, dependency bumps, refactors, code quality, infrastructure changes.

Look at previous releases for the format:

```bash
gh release view v0.8.0 --repo JarvusInnovations/gtfs-realtime-archiver --json body -q '.body'
```

### 4. Update the PR

```bash
gh pr edit {PR_NUMBER} --repo JarvusInnovations/gtfs-realtime-archiver \
  --title "Release: v{X.Y.Z}" \
  --body "$(cat <<'EOF'
## Improvements

- feat: description @author

## Technical

- fix: description @author
EOF
)"
```

Version bumping guidelines:

- **Patch** (0.8.0 → 0.8.1): bug fixes, dependency bumps, CI fixes
- **Minor** (0.8.0 → 0.9.0): new features, new data endpoints, new assets
- **Major**: breaking changes to the public data format or API

### 5. Verify CI passes

```bash
gh pr checks {PR_NUMBER} --repo JarvusInnovations/gtfs-realtime-archiver
```

All checks must pass before merging. If there are failures, fix on develop and push — the PR updates automatically.

### 6. Merge (user does this)

Do NOT merge the PR yourself. Tell the user it's ready and let them merge.

### 7. After merge — watch the automated release + deploy

A GitHub Action automatically creates a release tag after the PR merges to main, which triggers the deploy workflow. Watch both:

```bash
# Watch the release creation action
gh run list --repo JarvusInnovations/gtfs-realtime-archiver --limit 5
# Find the "Release: Publish" or similar workflow and watch it

# Then watch the deploy workflow that it triggers
gh run list --repo JarvusInnovations/gtfs-realtime-archiver --limit 5 --branch main
gh run watch {DEPLOY_RUN_ID} --repo JarvusInnovations/gtfs-realtime-archiver
```

The deploy workflow builds container images, pushes to GHCR + Artifact Registry, runs `tofu apply`, and verifies the health endpoint.

### 8. Verify and sync

```bash
# Verify the deployment
curl -s https://archiver.gtfsrt.io/health | jq .version

# Fetch the new tag locally
git fetch --tags
```

## Important

- **Never push to main.** Only push to develop. Main is protected and updated via the release PR.
- **Never merge without user approval.** Present the PR as ready and let the user decide.
- Prerelease tags (e.g., `v0.8.0-rc.1`) build and push containers but skip the deploy job.
- If tofu changes are needed before the release (e.g., BigQuery tables, IAM), apply them manually with `cd tf && tofu apply -concise` targeted at specific resources.
