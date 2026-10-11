"""Unit and integration tests for GitPulse Dashboard data loader and refresh bridge (Milestone 4)."""

from pathlib import Path
import pytest
import pandas as pd

from src.database import DatabaseManager
from src.dashboard import load_analytics_data, refresh_pipeline


@pytest.fixture
def test_dashboard_db(tmp_path):
    """Fixture providing a temporary SQLite database with test repository data."""
    db_file = tmp_path / "dashboard_test.db"
    db_manager = DatabaseManager(db_path=db_file)

    # Insert sample repository
    db_manager.upsert_repository(
        {
            "repo_id": 501,
            "owner": "test-org",
            "name": "dashboard-tool",
            "description": "Test repo for dashboard tests",
            "primary_language": "Python",
            "stars": 1500,
            "forks": 120,
            "open_issues": 15,
            "creation_date": "2022-01-01T00:00:00Z",
            "last_updated_date": "2026-10-10T00:00:00Z",
            "url": "https://github.com/test-org/dashboard-tool",
        }
    )

    # Insert sample snapshot
    db_manager.insert_snapshot(
        repo_id=501,
        stars=1500,
        forks=120,
        open_issues=15,
        collection_timestamp="2026-10-11T12:00:00Z",
    )

    # Insert sample pipeline run
    run_id = db_manager.start_pipeline_run()
    db_manager.complete_pipeline_run(
        run_id=run_id, status="COMPLETED", successful_repositories=1
    )

    return db_file


def test_load_analytics_data_nonexistent_db(tmp_path):
    """Test that loading from a nonexistent database returns empty DataFrames safely without crashing."""
    nonexistent = tmp_path / "does_not_exist.db"
    data = load_analytics_data(str(nonexistent))

    assert isinstance(data, dict)
    assert "top_repos" in data
    assert "languages" in data
    assert "activity" in data
    assert "history" in data
    assert "raw_repos" in data
    assert data["top_repos"].empty
    assert data["raw_repos"].empty


def test_load_analytics_data_populated_db(test_dashboard_db):
    """Test loading analytics datasets from a populated database."""
    data = load_analytics_data(str(test_dashboard_db))

    assert not data["top_repos"].empty
    assert len(data["top_repos"]) == 1
    assert data["top_repos"].iloc[0]["full_name"] == "test-org/dashboard-tool"
    assert data["top_repos"].iloc[0]["stars"] == 1500

    assert not data["languages"].empty
    assert data["languages"].iloc[0]["primary_language"] == "Python"

    assert not data["raw_snaps"].empty
    assert len(data["raw_snaps"]) == 1

    assert not data["pipeline_runs"].empty
    assert data["pipeline_runs"].iloc[0]["status"] == "COMPLETED"


def test_refresh_pipeline_execution(test_dashboard_db, tmp_path, monkeypatch):
    """Test that refresh_pipeline exports CSVs without error."""
    # Run refresh pipeline
    refresh_pipeline(str(test_dashboard_db))

    # Test that load_analytics_data reflects data correctly
    data = load_analytics_data(str(test_dashboard_db))
    assert not data["top_repos"].empty


def test_ensure_database_initialized(tmp_path):
    """Test that ensure_database_initialized creates missing DB and schema tables."""
    from src.dashboard import ensure_database_initialized

    missing_db = tmp_path / "subdir" / "new_gitpulse.db"
    assert not missing_db.exists()

    db_manager = ensure_database_initialized(str(missing_db))
    assert missing_db.exists()

    with db_manager.get_connection() as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_names = {t["name"] for t in tables}

    assert "repositories" in table_names
    assert "repository_snapshots" in table_names
    assert "pipeline_runs" in table_names


def test_trigger_ingestion_mocked(tmp_path):
    """Test intentional trigger_ingestion flow with mocked GitHub responses."""
    from unittest.mock import patch
    from src.dashboard import trigger_ingestion

    test_db = tmp_path / "trigger_test.db"

    mock_payload = {
        "id": 999123,
        "name": "sample-repo",
        "owner": {"login": "sample-owner"},
        "description": "Sample description",
        "language": "Python",
        "stargazers_count": 4200,
        "forks_count": 300,
        "open_issues_count": 12,
        "created_at": "2021-01-01T00:00:00Z",
        "updated_at": "2026-10-10T00:00:00Z",
        "html_url": "https://github.com/sample-owner/sample-repo",
    }

    with patch("src.ingest.GitHubIngester.fetch_repo_raw", return_value=mock_payload):
        with patch("src.ingest.GitHubIngester.check_rate_limit", return_value={"remaining": 50, "limit": 60}):
            success, msg = trigger_ingestion(["sample-owner/sample-repo"], str(test_db))

    assert success is True
    assert "Successfully ingested" in msg

    # Verify data is loaded into the database
    data = load_analytics_data(str(test_db))
    assert not data["top_repos"].empty
    assert data["top_repos"].iloc[0]["full_name"] == "sample-owner/sample-repo"


def test_trigger_ingestion_rate_limited(tmp_path):
    """Test trigger_ingestion handles exhausted rate limit gracefully."""
    from unittest.mock import patch
    from src.dashboard import trigger_ingestion

    test_db = tmp_path / "rate_limit_test.db"

    with patch("src.ingest.GitHubIngester.check_rate_limit", return_value={"remaining": 0, "reset_time_utc": "2026-10-11T22:00:00Z"}):
        success, msg = trigger_ingestion(["psf/requests"], str(test_db))

    assert success is False
    assert "rate limit reached" in msg.lower()
