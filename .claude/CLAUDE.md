# GTFS-RT Archiver - AI Assistant Guide

## Project Overview

See [README.md](../README.md) for complete project documentation including:

- Architecture and design philosophy
- Developer quickstart and setup
- Configuration reference
- Deployment instructions

See [DESIGN.md](../DESIGN.md) for detailed technical specifications.

## Documentation Maintenance

**CRITICAL**: Keep these files synchronized with code changes:

- **README.md**: User-facing documentation
  - Update when features are added/changed/removed
  - Keep configuration examples current
  - Update deployment instructions if infrastructure changes

- **CLAUDE.md** (this file): AI assistant guidelines
  - Update when repository structure changes
  - Keep commit practices current
  - Reflect any new conventions or patterns

- **DESIGN.md**: Technical specification
  - Update when architecture changes
  - Document deviations from original design

**Rule**: If a commit changes functionality, structure, or configuration, update relevant documentation in the same commit.

## Repository Layout

```
gtfs-realtime-archiver/
├── .github/workflows/      # CI/CD (lint, test, build, push, pages)
├── .claude/                # AI assistant guidelines (this directory)
├── .dagster_home/          # Dagster configuration
├── site/                   # Static site for gtfsrt.io (GitHub Pages)
│   ├── index.html          # Single-page site
│   ├── style.css           # Styles
│   ├── app.js              # Inventory fetch and render
│   ├── CNAME               # Custom domain: gtfsrt.io
│   └── favicon.svg         # Site icon
├── src/
│   ├── gtfs_rt_archiver/   # Archiver service
│   │   ├── __main__.py     # Application entry point and orchestration
│   │   ├── config.py       # Settings and YAML configuration loading
│   │   ├── models.py       # Pydantic data models
│   │   ├── fetcher.py      # HTTP client with retry logic
│   │   ├── storage.py      # GCS writer with Hive partitioning
│   │   ├── scheduler.py    # APScheduler job scheduling
│   │   ├── metrics.py      # Prometheus metrics definitions
│   │   ├── logging.py      # Structlog configuration
│   │   └── health.py       # Health/metrics HTTP server
│   └── dagster_pipeline/   # Data processing pipeline
│       ├── definitions.py  # Dagster definitions entry point
│       └── defs/           # Pipeline definitions
├── tests/                  # pytest test suite
│   ├── conftest.py         # Shared fixtures
│   ├── test_*.py           # Module-specific tests
│   └── __init__.py
├── tf/                     # OpenTofu/Terraform for Cloud Run
│   ├── main.tf             # Cloud Run service (archiver)
│   ├── storage.tf          # GCS bucket with lifecycle
│   ├── iam.tf              # Service account and permissions
│   ├── cloudsql.tf         # Cloud SQL PostgreSQL instance
│   ├── dagster.tf          # Dagster module instantiation (project wiring via extra_env/grants)
│   ├── dagster_moved.tf    # State moves from the module's generalization (delete after applied)
│   ├── modules/dagster/    # Dagster deployment module (generic — being extracted to a registry module)
│   │   ├── main.tf         # Module locals and config
│   │   ├── webserver.tf    # Dagster UI (Cloud Run Service, split mode)
│   │   ├── daemon.tf       # Dagster daemon (Worker Pool, split mode)
│   │   ├── code_server.tf  # gRPC code servers (split mode)
│   │   ├── consolidated.tf # Single-instance web+daemon+code (consolidated + on-demand modes)
│   │   ├── run_worker.tf   # Cloud Run Jobs for runs
│   │   ├── iam.tf          # Service accounts, permissions + generic bucket/secret grants
│   │   ├── hmac.tf         # Optional per-run-worker GCS HMAC keys (dbt-duckdb httpfs)
│   │   ├── secrets.tf      # DB password secret
│   │   ├── database.tf     # Database and user creation
│   │   └── storage.tf      # Logs bucket
│   ├── variables.tf        # Input variables
│   ├── outputs.tf          # Output values
│   └── versions.tf         # Provider versions
├── deploy/                 # Dagster deployment configs
│   ├── dagster.yaml        # Dagster config with env var placeholders
│   └── workspace.yaml      # Workspace config with env var placeholders
├── pyproject.toml          # Project config, dependencies, tool settings
├── uv.lock                 # Dependency lockfile
├── Dockerfile              # Multi-stage container build (archiver)
├── Containerfile.dagster   # Multi-target build (webserver, daemon, code-server)
├── agencies.example.yaml   # Example agency configuration
├── .env.example            # Environment variables template
└── .tool-versions          # asdf version pins
```

**Dependency Groups** (in pyproject.toml):

- `archiver` / `dev-archiver` - deps for gtfs_rt_archiver
- `dagster` / `dev-dagster` - deps for dagster_pipeline (local dev)
- `dagster-deploy` - deps for Dagster Cloud Run deployment
- `dev` - aggregate group (archiver + dagster dev groups + mypy, ruff)

**Managing Dependencies**:

**CRITICAL**: ALWAYS use `uv add --group <group> <package>`. NEVER edit pyproject.toml directly.

Why: `uv add` resolves the latest compatible version and updates uv.lock atomically. Manual edits may install outdated versions or create lock inconsistencies.

**Examples**:

- `uv add --group archiver httpx`
- `uv add --group dev-archiver pytest-asyncio`
- `uv add --group dagster dagster-gcp`

**Key Patterns**:

- All code uses async/await (httpx, gcloud-aio-storage, aiohttp)
- Type hints enforced via mypy strict mode
- Pydantic for all configuration and data validation
- Structured logging via structlog (JSON in prod, text in dev)

## Commit Practice

This project uses **Conventional Commits** with components:

**Commit Types** (only use these 5):

- `feat` - New features
- `fix` - Bug fixes
- `chore` - Maintenance (dependencies, config, tooling)
- `test` - Test additions or modifications
- `docs` - Documentation updates

**Components** (optional scope):

- `archiver` - Archiver service code
- `dagster` - Dagster pipeline code
- `tf` - Infrastructure/Terraform
- `ci` - GitHub Actions workflows
- `docker` - Dockerfile and container
- `site` - Static site (gtfsrt.io)
- `claude` - AI assistant documentation

**Format**: `type(component): description`

**Examples**:

- `feat(archiver): add HTTP fetcher with retry logic`
- `fix(archiver): add async lock to prevent race condition`
- `chore(deps): add core dependencies`
- `test(archiver): add tests for configuration loading`
- `docs(claude): add AI assistant guide`
- `feat(tf): add OpenTofu configuration for Cloud Run`
- `chore(docker): add multi-stage Dockerfile`

### Commit Guidelines

**Planning**:

- Group related changes into logical, incremental commits
- Each commit should be a coherent unit of work
- Prefer multiple small commits over one large commit

**Workflow**:

1. When commands modify the worktree (e.g., `uv add`, `npm install`), commit those changes immediately:

   ```
   chore: add tenacity dependency

   Add tenacity for retry logic with exponential backoff.

   Ran: uv add tenacity
   ```

2. Make manual code changes for the next logical unit
3. Run tests before committing: `uv run pytest tests/`
4. Commit with descriptive message

**Quality Checks** (must pass before commit):

- `uv run ruff check src/ tests/` - Linting
- `uv run mypy src/` - Type checking
- `uv run pytest tests/` - All tests

**Commit Message Format**:

```
type(component): brief description (50 chars or less)

More detailed explanation if needed. Wrap at 72 characters.
Include "Ran: {command}" for generated changes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Sonnet 4.5 (1M context) <noreply@anthropic.com>
```

**Notes**:

- Component is optional but recommended for clarity
- Omit component for cross-cutting changes (e.g., `chore: update dependencies`)
- Use present tense ("add" not "added")
- First line should be imperative ("add X" not "adds X")

### Reference

View commit history: `git log --oneline`

## OpenTofu (Infrastructure)

**Directory**: `tf/`

**Project**: `gtfs-archiver` (GCP)

**Commands** (always use `-concise`):

```bash
cd tf/
tofu init                    # First time / after provider changes
tofu plan -concise           # Preview changes
tofu apply -concise          # Apply changes (will prompt)
tofu apply -concise -auto-approve  # Apply without prompt
```

**Targeting specific resources**:

```bash
tofu apply -concise -target=google_storage_bucket.archive
```

**State**: Stored in `gs://gtfs-archiver-tf-state`

## Dagster (Pipeline)

**Directory**: `src/dagster_pipeline/`

**Commands**:

```bash
# Start Dagster UI (dev server)
uv run dg dev

# List all definitions (assets, schedules, resources)
uv run dg list defs

# Validate definitions load correctly
uv run dg check defs

# Launch a run for specific assets with partition
uv run dg launch --assets vehicle_positions_parquet --partition 2026-01-01

# Launch all assets for a partition
uv run dg launch --partition 2026-01-01
```

**Environment Setup**:

- Set `DAGSTER_HOME` to an absolute path (required)
- Required env vars: `GCS_BUCKET_RT_PROTOBUF`, `GCS_BUCKET_RT_PARQUET`, `GCP_PROJECT_ID`

**Local Development**:

- UI available at `http://localhost:3000` when running `dg dev`
- Logs stored in `.dagster_home/storage/*/compute_logs/`
- Run history in `.dagster_home/history/`

**Service Account for Local Dev**:

Use the run worker SA for dev/prod parity:

```bash
# Get run worker SA for gtfsrt code location
export DAGSTER_SA=$(cd tf && tofu output -json dagster_run_worker_service_account_emails | jq -r '.gtfsrt')

# Impersonate for local development
gcloud auth application-default login --impersonate-service-account=$DAGSTER_SA
```

Why run worker SA?

- Local dev executes pipeline code (assets/ops)
- Run worker SA is what executes this code in production
- Ensures same permissions locally and in production

## Dagster Cloud Run Deployment

**Architecture**: Dagster deployed to Google Cloud Run with:

- **Webserver** (Cloud Run Service) - Dagster UI on port 3000
- **Daemon** (Cloud Run Worker Pool BETA) - Scheduler/sensors, exactly 1 instance
- **Code Server** (Cloud Run Service) - gRPC API server on port 3030
- **Run Workers** (Cloud Run Jobs) - Execute runs via CloudRunRunLauncher

**Database**: Cloud SQL PostgreSQL via Unix socket mount (`/cloudsql/{connection}`)

**Configuration**: Baked into container images (from `deploy/` directory)

- Config files use environment variable placeholders
- Values resolved at runtime from Terraform-provided env vars

**Terraform Module**: `tf/modules/dagster/`

- Single code location (gtfsrt) by default
- Extensible to multi-code-location via `code_locations` variable
- Per-location service accounts for fine-grained IAM

**Container Images** (via Containerfile.dagster):

- `dagster-webserver` - Runs `dagster-webserver --host 0.0.0.0 --port 3000`
- `dagster-daemon` - Runs `dagster-daemon run`
- `dagster-code-server` - Runs `dagster api grpc --module-name dagster_pipeline.definitions`

**Multi-Code-Location Support**:

To add a new code location:

1. Update `tf/dagster.tf` module instantiation:

```hcl
code_locations = {
  gtfsrt = { ... }
  new_location = {
    image             = "..."
    module_name       = "new_pipeline.definitions"
    port              = 3031
    run_worker_cpu    = "2"
    run_worker_memory = "4Gi"
  }
}
```

1. Update `deploy/workspace.yaml` to add new code location
2. Build and push new code location image

Each location gets:

- Dedicated code server (gRPC)
- Dedicated run worker job
- Dedicated service account with specific IAM permissions

**Deployment Topologies** (`deployment_mode` variable):

The module supports three topologies, selected via `dagster_deployment_mode`
(root) / `deployment_mode` (module). Default is `split`.

- **`split`** (default): webserver, daemon, and code server each run as their
  own Cloud Run resource (Service / Worker Pool / Service). The webserver scales
  0→N and the code server is isolated so code reloads don't affect the host
  processes. Use when you need horizontal UI scaling or multiple code locations.

- **`consolidated`**: webserver (ingress) + daemon + code server run as three
  containers in **one always-on Cloud Run Service instance** (`consolidated.tf`),
  for a single code location. Lowest cost floor — collapses what is otherwise two
  always-on footprints (daemon + daemon-kept-warm code server) into one. Run
  workers are unchanged (still per-run Cloud Run Jobs).

  Constraints baked into the consolidated service:
  - `max_instance_count = 1` — the daemon must be a singleton (a second instance
    would double-fire schedules/sensors). Caveat: unlike the split Worker Pool's
    MANUAL scaling, a Service revision rollout can briefly run old + new instances
    concurrently, so the daemon may transiently double-fire during deploys —
    acceptable for idempotent schedules, worth knowing about.
  - `cpu_idle = false` (instance-based billing) — in a request-billed Service,
    sidecars only get CPU while the ingress handles a request, which starves the
    always-on daemon. Always-allocated CPU is required.
  - The code server is reached over `localhost` (`CODE_SERVER_HOST_<LOC>=localhost`,
    port from `deploy/workspace.yaml`); no internal code-server Service is created.

  Flip topologies with `dagster_deployment_mode = "consolidated"` in tfvars and
  `tofu apply`. Switching destroys the resources of the other topology and creates
  the active one; the database, buckets, secrets, run-worker job, and service
  accounts are shared across both.

  **Cost break-even**: at the default sizing (containers summing to 1 vCPU / 2Gi)
  the consolidated instance runs ~$55/mo — vs ~$100/mo idle for the split topology
  (always-on daemon + daemon-kept-warm code server). Sized up to 2 vCPU / 2.5Gi it
  is ~$105–110/mo, a wash against split. Consolidation saves money only at roughly
  ≤1.5 vCPU total; see the note on `consolidated_resources` in
  `tf/modules/dagster/variables.tf`.

- **`on-demand`**: the **same single-instance topology as `consolidated`** (reuses
  `consolidated.tf` and `consolidated_resources`) but with `min_instance_count = 0`.
  It scales to zero when idle and cold-starts on the next UI visit. `cpu_idle` stays
  `false`, which is the key: Cloud Run scales to zero on absence of *requests*, not
  CPU, so while the instance is up — including the ~15 min idle window before it
  scales down — the daemon has full CPU and reliably drains the run queue / launches
  runs, even if the user closes the tab right after clicking Launch. A launched run
  executes in its own Cloud Run Job and keeps going (writing status to Postgres)
  after the UI instance scales to zero.

  Use for **demo / occasional-manual-run instances**: pay only while someone is
  using it (session + the idle window), $0 otherwise. Cloud SQL then becomes the
  dominant remaining cost. Trade-offs:
  - **Not for scheduled/sensor workloads** — schedules and sensors only fire while
    someone has the UI open (the instance is at zero the rest of the time). This
    mode assumes manual, UI-triggered runs only.
  - Every session pays a cold start (all three containers, gated by the code
    server's gRPC startup probe). The code server is still packed as a sidecar
    container; an optional future optimization is loading code in-process (drop the
    code-server container, `python_module` workspace) to shave cold-start time, at
    the cost of prod-parity.
  - Deferred housekeeping: run-monitoring / retries / zombie-run reaping only run
    when the daemon is awake, i.e. on the next visit.

  Set `dagster_deployment_mode = "on-demand"`. No `deploy/` changes required — it
  works with the existing baked `dagster.yaml`/`workspace.yaml` (QueuedRunCoordinator
  is fine, since the daemon has CPU during the up-window).

**Terraform image variables move with releases — never apply with stale ones**:

The release workflow (`.github/workflows/deploy.yaml`) deploys by running
`tofu apply` with `-var` image values derived from the release tag. Terraform
*is* the image mover, so the image fields deliberately have **no**
`lifecycle ignore_changes` (that would silently break CI deploys). The corollary:
a local `tofu plan`/`apply` that doesn't pass the currently-deployed image
versions will show (and would roll back!) image "downgrades" to whatever stale
values are in tfvars/defaults. Before any local apply, derive the image vars from
the latest release tag (as deploy.yaml does), pass `-target` for the resources
you're changing, or confirm the plan shows no image changes.

## Testing Container Builds

**When to test locally**:

- Changes to `Containerfile.dagster` or `Dockerfile`
- Adding/removing dependencies in `pyproject.toml`
- Adding imports between `dagster_pipeline` and `gtfs_rt_archiver`
- Modifying package structure in `src/`

**Build and test the Dagster code-server** (most common):

```bash
# Build the code-server target
docker build -f Containerfile.dagster --target code-server -t dagster-code-server:test .

# Run with required env vars to verify startup
docker run --rm \
  -e GCS_BUCKET_RT_PROTOBUF=test \
  -e GCS_BUCKET_RT_PARQUET=test \
  -e GCP_PROJECT_ID=test \
  dagster-code-server:test

# Expected output (success):
# Starting Dagster code server for module dagster_pipeline.definitions on port 3030
# Started Dagster code server for module dagster_pipeline.definitions on port 3030
```

**Other targets**:

```bash
# Webserver
docker build -f Containerfile.dagster --target webserver -t dagster-webserver:test .

# Daemon
docker build -f Containerfile.dagster --target daemon -t dagster-daemon:test .

# Archiver service
docker build -f Dockerfile -t gtfs-rt-archiver:test .
```

**Common issues**:

- `ModuleNotFoundError` - Check that source is copied to `src/` directory and `uv sync` installs the project (not just deps)
- Missing dependencies - Ensure the dependency group includes all required packages (e.g., `dagster-deploy` includes `archiver` group)
