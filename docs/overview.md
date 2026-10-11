# GitPulse - Local-First GitHub Analytics ELT Pipeline

## Overview
GitPulse is a local-first GitHub analytics tool designed to extract public repository metadata, load raw payloads, transform structured metrics, and present interactive analytics dashboards.

## Milestone 1: Ingestion
In the first milestone, the ingestion engine connects to the unauthenticated GitHub REST API (`https://api.github.com/repos/{owner}/{repo}`) to collect core repository metadata.

### Data Ingested
- Repository Full Name (`name`, `owner`)
- Numeric Repository ID (`repo_id`)
- Description (`description`)
- Primary Language (`primary_language`)
- Star Count (`stars`)
- Fork Count (`forks`)
- Open Issue Count (`open_issues`)
- Creation Date (`created_at`)
- Last Updated Date (`updated_at`)
- HTML URL (`url`)

### Key Architecture Details
- **Unauthenticated GitHub REST API**: Uses standard `User-Agent` headers without requiring an API key or personal access token.
- **Robust Network & Rate-Limit Handling**: Catches HTTP 403 / 429 rate limit errors (inspecting `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers), 404 not found, and network dropouts.
- **Raw Storage**: Stores complete raw JSON responses locally in `data/raw/` preserving raw fidelity for future ELT transformation steps.
- **Clean Schema Extraction**: Parses the raw JSON into normalized Python dictionaries with fallback for missing or null fields.

## Milestone 2: SQLite Database & Incremental Loading
Milestone 2 adds local SQLite database persistence at `data/gitpulse.db`.

### Database Schema
1. **`repositories`**:
   - `repo_id` (INTEGER PRIMARY KEY, stable GitHub numeric ID)
   - `owner`, `name`, `description`, `primary_language`
   - `stars`, `forks`, `open_issues`
   - `creation_date`, `last_updated_date`, `url`, `ingested_at`
2. **`repository_snapshots`**:
   - `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
   - `repo_id` (INTEGER NOT NULL, FOREIGN KEY referencing `repositories(repo_id)` with `ON DELETE CASCADE`)
   - `collection_timestamp`, `stars`, `forks`, `open_issues`
3. **`pipeline_runs`**:
   - `run_id` (INTEGER PRIMARY KEY AUTOINCREMENT)
   - `start_time`, `end_time`, `status` (`RUNNING`, `COMPLETED`, `PARTIAL`, `FAILED`), `successful_repositories`, `error_details`

### Key Features
- **Idempotent Incremental Loading (UPSERT)**: Re-running ingestion updates existing repository records without inserting duplicates.
- **Time-Series Snapshots**: Each successful collection creates a historical snapshot so repository evolution can be tracked over time.
- **Error Audit Logging**: Pipeline runs record status and specific error messages when failures occur.

## Milestone 3: Data Transformation & Analytics
Milestone 3 implements read-only SQL and pandas transformation workflows that turn raw database tables into analytics datasets stored in `data/processed/`.

### Generated Analytics Views
1. **`top_repositories.csv`**:
   - Ranks all repositories by stars (`star_rank`) and forks (`fork_rank`).
2. **`language_summary.csv`**:
   - Aggregates repositories by programming language (`repo_count`, `total_stars`, `avg_stars`, `total_forks`, `avg_forks`).
   - Handles missing or empty language attributes safely as `"Unknown"`.
3. **`repository_activity.csv`**:
   - Calculates repository age in days (`age_days`), days since last GitHub update (`days_since_update`), and health ratios (`issue_to_star_pct`, `fork_to_star_pct`).
4. **`historical_changes.csv`**:
   - Computes metric changes (`delta_stars`, `delta_forks`, `delta_open_issues`) across snapshots using real collection timestamps.
   - If a repository has only 1 snapshot, explicitly marks status as `INSUFFICIENT_DATA` with a descriptive note.

## Milestone 4: Interactive Analytics Dashboard
Milestone 4 delivers an interactive Streamlit and Plotly dashboard with a dark navy data-product theme:
- **Overview KPIs**: Total repositories, aggregated stars, ecosystem forks, open issues backlog, and top repository.
- **Repository Rankings**: Search filters, language selectors, minimum star controls, side-by-side grouped horizontal bar charts, and data tables.
- **Language Analytics**: Donut share distribution, total stars, and average stars bar charts.
- **Repository Activity**: Open issues vs. star density scatter plot and days-since-update latency charts.
- **Historical Snapshot Trends**: Multi-trace time-series timeline for stars, forks, and issues across actual collection timestamps with single-snapshot safeguard notifications.
- **On-Demand Refresh**: Interactive sidebar button to trigger transformation recalibration and export CSV updates.

---

### Command Cheat Sheet

```powershell
# 1. Activate Virtual Environment
.\.venv\Scripts\Activate.ps1

# 2. Ingest Public Repositories (Unauthenticated)
python -m src.ingest --repos pallets/flask psf/requests fastapi/fastapi

# 3. Inspect Local SQLite Database
python -m src.database --repos
python -m src.database --snapshots
python -m src.database --runs

# 4. Run Transformations & Export CSVs
python -m src.transform

# 5. Launch Interactive Analytics Dashboard
streamlit run src.dashboard.py

# 6. Run Complete Test Suite (Milestones 1-4)
python -m pytest -v
```
