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
├── .github/workflows/      # CI/CD (lint, test, build, push)
├── .claude/                # AI assistant guidelines (this directory)
├── .dagster_home/          # Dagster configuration
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
│   ├── main.tf             # Cloud Run service
│   ├── storage.tf          # GCS bucket with lifecycle
│   ├── iam.tf              # Service account and permissions
│   ├── variables.tf        # Input variables
│   ├── outputs.tf          # Output values
│   └── versions.tf         # Provider versions
├── pyproject.toml          # Project config, dependencies, tool settings
├── uv.lock                 # Dependency lockfile
├── Dockerfile              # Multi-stage container build (archiver)
├── agencies.example.yaml   # Example agency configuration
├── .env.example            # Environment variables template
└── .tool-versions          # asdf version pins
```

**Dependency Groups** (in pyproject.toml):

- `archiver` / `dev-archiver` - deps for gtfs_rt_archiver
- `dagster` / `dev-dagster` - deps for dagster_pipeline
- `dev` - aggregate group (all of the above + mypy, ruff)

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
