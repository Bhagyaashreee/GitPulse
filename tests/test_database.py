"""Unit and integration tests for GitPulse Database Manager & Pipeline Tracking (Milestone 2).

All tests use temporary directories (tmp_path) to ensure the project database is never modified.
"""

import sqlite3
from unittest.mock import MagicMock, patch
import pytest

from src.database import DatabaseManager
from src.ingest import GitHubIngester, GitHubRepoNotFoundError


@pytest.fixture
def temp_db(tmp_path):
    """Fixture providing an isolated DatabaseManager instance in tmp_path."""
    db_file = tmp_path / "test_gitpulse.db"
    return DatabaseManager(db_path=db_file)


@pytest.fixture
def sample_metrics():
    """Standard repository metrics dictionary for database tests."""
    return {
        "repo_id": 4164482,
        "name": "flask",
        "owner": "pallets",
        "description": "The Python micro framework for building web applications.",
        "primary_language": "Python",
        "stars": 75000,
        "forks": 17000,
        "open_issues": 10,
        "creation_date": "2010-04-06T11:11:59Z",
        "last_updated_date": "2026-10-11T12:00:00Z",
        "url": "https://github.com/pallets/flask",
    }


def test_db_initialization(temp_db):
    """Test that all three tables and indexes are created properly upon initialization."""
    with temp_db.get_connection() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
        table_names = {t["name"] for t in tables}

    assert "repositories" in table_names
    assert "repository_snapshots" in table_names
    assert "pipeline_runs" in table_names


def test_insert_and_get_repository(temp_db, sample_metrics):
    """Test inserting a new repository record and retrieving it."""
    temp_db.upsert_repository(sample_metrics)
    record = temp_db.get_repository(4164482)

    assert record is not None
    assert record["repo_id"] == 4164482
    assert record["name"] == "flask"
    assert record["owner"] == "pallets"
    assert record["stars"] == 75000
    assert record["primary_language"] == "Python"
    assert record["url"] == "https://github.com/pallets/flask"


def test_upsert_updates_existing_record_and_prevents_duplicates(temp_db, sample_metrics):
    """Test that re-running ingestion updates the existing repository without duplicate rows."""
    temp_db.upsert_repository(sample_metrics)

    # Re-insert with updated stars, forks, and updated_at
    updated_metrics = dict(sample_metrics)
    updated_metrics["stars"] = 75500
    updated_metrics["forks"] = 17100
    updated_metrics["last_updated_date"] = "2026-10-11T13:00:00Z"

    temp_db.upsert_repository(updated_metrics)

    all_repos = temp_db.get_all_repositories()
    assert len(all_repos) == 1  # No duplicate rows

    record = temp_db.get_repository(4164482)
    assert record["stars"] == 75500
    assert record["forks"] == 17100
    assert record["last_updated_date"] == "2026-10-11T13:00:00Z"


def test_insert_snapshot_history(temp_db, sample_metrics):
    """Test historical metric snapshots over multiple collection runs."""
    temp_db.upsert_repository(sample_metrics)

    # First snapshot
    snap1_id = temp_db.insert_snapshot(
        repo_id=4164482,
        stars=75000,
        forks=17000,
        open_issues=10,
        collection_timestamp="2026-10-11T12:00:00Z",
    )
    # Second snapshot (e.g. 1 hour later with gained stars)
    snap2_id = temp_db.insert_snapshot(
        repo_id=4164482,
        stars=75050,
        forks=17010,
        open_issues=8,
        collection_timestamp="2026-10-11T13:00:00Z",
    )

    snapshots = temp_db.get_snapshots(repo_id=4164482)
    assert len(snapshots) == 2
    assert snapshots[0]["id"] == snap1_id
    assert snapshots[0]["stars"] == 75000
    assert snapshots[1]["id"] == snap2_id
    assert snapshots[1]["stars"] == 75050


def test_foreign_key_constraint(temp_db):
    """Test that inserting a snapshot for a nonexistent repository raises IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        temp_db.insert_snapshot(
            repo_id=999999999,  # Non-existent repository
            stars=10,
            forks=2,
            open_issues=0,
        )


def test_foreign_key_cascade_delete(temp_db, sample_metrics):
    """Test that deleting a repository cascades and cleans up its snapshots."""
    temp_db.upsert_repository(sample_metrics)
    temp_db.insert_snapshot(repo_id=4164482, stars=75000, forks=17000, open_issues=10)

    snapshots_before = temp_db.get_snapshots(repo_id=4164482)
    assert len(snapshots_before) == 1

    with temp_db.get_connection() as conn:
        conn.execute("DELETE FROM repositories WHERE repo_id = ?", (4164482,))

    snapshots_after = temp_db.get_snapshots(repo_id=4164482)
    assert len(snapshots_after) == 0


def test_pipeline_run_success(temp_db):
    """Test tracking a successful pipeline execution."""
    run_id = temp_db.start_pipeline_run()
    assert run_id > 0

    runs_in_progress = temp_db.get_pipeline_runs()
    assert runs_in_progress[0]["status"] == "RUNNING"
    assert runs_in_progress[0]["end_time"] is None

    temp_db.complete_pipeline_run(
        run_id=run_id,
        status="COMPLETED",
        successful_repositories=3,
    )

    completed_runs = temp_db.get_pipeline_runs()
    assert completed_runs[0]["status"] == "COMPLETED"
    assert completed_runs[0]["successful_repositories"] == 3
    assert completed_runs[0]["end_time"] is not None
    assert completed_runs[0]["error_details"] is None


def test_pipeline_run_with_errors(temp_db):
    """Test recording errors and partial/failed status in pipeline_runs."""
    run_id = temp_db.start_pipeline_run()
    errors = ["fake/repo: Repository fake/repo was not found (HTTP 404)."]

    temp_db.complete_pipeline_run(
        run_id=run_id,
        status="PARTIAL",
        successful_repositories=1,
        error_details=errors,
    )

    runs = temp_db.get_pipeline_runs()
    assert runs[0]["status"] == "PARTIAL"
    assert runs[0]["successful_repositories"] == 1
    assert "404" in runs[0]["error_details"]


def test_ingest_with_database_integration(tmp_path, temp_db):
    """Test end-to-end ingestion saving to database and disk simultaneously."""
    ingester = GitHubIngester(
        raw_data_dir=tmp_path / "raw",
        db_manager=temp_db,
    )

    mock_raw_payload = {
        "id": 1362490,
        "name": "requests",
        "owner": {"login": "psf"},
        "description": "Python HTTP for humans.",
        "language": "Python",
        "stargazers_count": 52000,
        "forks_count": 9200,
        "open_issues_count": 140,
        "created_at": "2011-02-13T18:38:17Z",
        "updated_at": "2026-10-10T12:00:00Z",
        "html_url": "https://github.com/psf/requests",
    }

    with patch.object(ingester, "fetch_repo_raw", return_value=mock_raw_payload):
        results = ingester.ingest_multiple(["psf/requests"])

    assert len(results) == 1
    # Check repository saved in DB
    saved_repo = temp_db.get_repository(1362490)
    assert saved_repo is not None
    assert saved_repo["name"] == "requests"

    # Check snapshot created in DB
    snapshots = temp_db.get_snapshots(1362490)
    assert len(snapshots) == 1
    assert snapshots[0]["stars"] == 52000

    # Check pipeline run logged in DB
    runs = temp_db.get_pipeline_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "COMPLETED"
    assert runs[0]["successful_repositories"] == 1


def test_ingest_error_does_not_create_snapshot(tmp_path, temp_db):
    """Verify that failed API calls do not create orphan snapshots."""
    ingester = GitHubIngester(
        raw_data_dir=tmp_path / "raw",
        db_manager=temp_db,
    )

    with patch.object(
        ingester,
        "fetch_repo_raw",
        side_effect=GitHubRepoNotFoundError("Repository not found (404)"),
    ):
        results = ingester.ingest_multiple(["nonexistent/repo"])

    assert len(results) == 0
    # No repositories or snapshots should exist
    assert len(temp_db.get_all_repositories()) == 0
    assert len(temp_db.get_snapshots()) == 0

    # But pipeline run should record the failure
    runs = temp_db.get_pipeline_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "FAILED"
    assert runs[0]["successful_repositories"] == 0
    assert "404" in runs[0]["error_details"]
