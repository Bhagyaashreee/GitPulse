"""Unit and integration tests for GitPulse Dashboard data loader and refresh bridge (Milestone 4)."""

from pathlib import Path
import pytest
import pandas as pd

from src.database import DatabaseManager
from src.dashboard import (
    load_analytics_data,
    refresh_pipeline,
    render_header,
    render_historical_trends,
    render_repository_activity,
)


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


def test_render_historical_trends_with_multi_snapshots(tmp_path):
    """Test dashboard historical trend rendering across multiple chronological snapshots."""
    from unittest.mock import MagicMock, patch

    test_db = tmp_path / "trends_multi.db"
    db_mgr = DatabaseManager(db_path=test_db)

    # Insert sample repository
    db_mgr.upsert_repository(
        {
            "repo_id": 701,
            "owner": "trend-org",
            "name": "pulse-analytics",
            "description": "Multi snapshot trend testing",
            "primary_language": "Python",
            "stars": 1100,
            "forks": 110,
            "open_issues": 8,
            "creation_date": "2021-01-01T00:00:00Z",
            "last_updated_date": "2026-10-10T00:00:00Z",
            "url": "https://github.com/trend-org/pulse-analytics",
        }
    )

    # Insert 3 chronological snapshots
    db_mgr.insert_snapshot(
        repo_id=701, stars=1000, forks=100, open_issues=10, collection_timestamp="2026-10-01T00:00:00Z"
    )
    db_mgr.insert_snapshot(
        repo_id=701, stars=1050, forks=105, open_issues=9, collection_timestamp="2026-10-02T00:00:00Z"
    )
    db_mgr.insert_snapshot(
        repo_id=701, stars=1100, forks=110, open_issues=8, collection_timestamp="2026-10-03T00:00:00Z"
    )

    data = load_analytics_data(str(test_db))
    assert not data["raw_snaps"].empty
    assert len(data["raw_snaps"]) == 3

    # Mock streamlit components to verify render_historical_trends executes without exceptions
    with patch("src.dashboard.st") as mock_st:
        mock_st.selectbox.return_value = "trend-org/pulse-analytics"
        mock_st.columns.return_value = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
        mock_st.radio.return_value = "All Metrics"

        render_historical_trends(data)

        # Verify selector, metrics, plotly chart, and dataframe were rendered
        assert mock_st.selectbox.called
        assert mock_st.plotly_chart.called
        assert mock_st.dataframe.called


def test_render_historical_trends_single_and_zero_snapshot_fallbacks(tmp_path):
    """Test dashboard historical trends fallbacks for 0 snapshots, 1 snapshot, and empty database."""
    from unittest.mock import patch

    test_db = tmp_path / "trends_fallbacks.db"
    db_mgr = DatabaseManager(db_path=test_db)

    # Repo 801: 1 snapshot
    db_mgr.upsert_repository(
        {
            "repo_id": 801,
            "owner": "single-org",
            "name": "single-snap",
            "description": "Repo with single snapshot",
            "primary_language": "Python",
            "stars": 500,
            "forks": 50,
            "open_issues": 5,
            "creation_date": "2022-01-01T00:00:00Z",
            "last_updated_date": "2026-10-10T00:00:00Z",
            "url": "https://github.com/single-org/single-snap",
        }
    )
    db_mgr.insert_snapshot(
        repo_id=801, stars=500, forks=50, open_issues=5, collection_timestamp="2026-10-01T00:00:00Z"
    )

    # Repo 802: 0 snapshots
    db_mgr.upsert_repository(
        {
            "repo_id": 802,
            "owner": "zero-org",
            "name": "zero-snap",
            "description": "Repo with zero snapshots",
            "primary_language": "Rust",
            "stars": 300,
            "forks": 30,
            "open_issues": 3,
            "creation_date": "2023-01-01T00:00:00Z",
            "last_updated_date": "2026-10-10T00:00:00Z",
            "url": "https://github.com/zero-org/zero-snap",
        }
    )

    data = load_analytics_data(str(test_db))

    # 1. Test single snapshot repository fallback
    with patch("src.dashboard.st") as mock_st:
        mock_st.selectbox.return_value = "single-org/single-snap"
        render_historical_trends(data)
        assert mock_st.info.called
        # Check that informative single-snapshot guidance was output
        assert any("Only 1 snapshot recorded" in str(arg) for call in mock_st.info.call_args_list for arg in call[0])

    # 2. Test zero snapshot repository fallback
    with patch("src.dashboard.st") as mock_st:
        mock_st.selectbox.return_value = "zero-org/zero-snap"
        render_historical_trends(data)
        assert mock_st.warning.called
        assert any("No historical snapshots found" in str(arg) for call in mock_st.warning.call_args_list for arg in call[0])

    # 3. Test empty database fallback
    empty_data = {
        "top_repos": pd.DataFrame(),
        "raw_snaps": pd.DataFrame(),
        "history": pd.DataFrame(),
        "health": pd.DataFrame(),
    }
    with patch("src.dashboard.st") as mock_st:
        render_historical_trends(empty_data)
        assert mock_st.info.called


def test_dashboard_health_data_loading(test_dashboard_db):
    """Test that load_analytics_data includes health scores and correct columns."""
    data = load_analytics_data(str(test_dashboard_db))

    assert "health" in data
    health_df = data["health"]
    assert not health_df.empty
    assert "health_score" in health_df.columns
    assert "health_grade" in health_df.columns
    assert "data_coverage_pct" in health_df.columns
    assert "recency_detail" in health_df.columns


def test_render_repository_activity_with_health_scoring(test_dashboard_db):
    """Test render_repository_activity renders health scoreboard, metrics, and expander without errors."""
    from unittest.mock import MagicMock, patch

    data = load_analytics_data(str(test_dashboard_db))

    with patch("src.dashboard.st") as mock_st:
        mock_st.selectbox.return_value = data["health"].iloc[0]["full_name"]
        mock_st.columns.side_effect = lambda spec: [MagicMock() for _ in range(len(spec) if isinstance(spec, list) else int(spec))]
        mock_expander = MagicMock()
        mock_st.expander.return_value.__enter__.return_value = mock_expander

        render_repository_activity(data)

        # Verify dataframe scoreboard, metrics, and charts were rendered
        assert mock_st.dataframe.called
        assert mock_st.metric.called
        assert mock_st.plotly_chart.called


def test_render_header_success_and_failure_states():
    """Test render_header renders cleanly for both COMPLETED and FAILED states without raw HTML code blocks."""
    from unittest.mock import MagicMock, patch

    # Case 1: Latest run is COMPLETED (e.g. Run 4)
    completed_runs = pd.DataFrame(
        [
            {
                "run_id": 4,
                "status": "COMPLETED",
                "successful_repositories": 3,
                "error_details": None,
            },
            {
                "run_id": 3,
                "status": "FAILED",
                "successful_repositories": 0,
                "error_details": "404 not found",
            },
        ]
    )

    with patch("src.dashboard.st") as mock_st:
        mock_st.columns.return_value = [MagicMock(), MagicMock()]
        render_header(completed_runs)

        # Verify that either st.html or st.markdown was called
        calls = mock_st.html.call_args_list if mock_st.html.called else mock_st.markdown.call_args_list
        assert len(calls) > 0
        rendered_texts = [str(c[0][0]) for c in calls]
        combined = " ".join(rendered_texts)
        assert "Latest Run #4: COMPLETED" in combined
        assert "badge-pill-success" in combined
        assert "Total Runs: 2" in combined
        # Verify no unintended indentation or raw code block artifacts
        assert "\n    " not in combined or "```" not in combined

    # Case 2: Latest run is FAILED with prior COMPLETED run (e.g. Run 3 FAILED, Run 2 COMPLETED)
    failed_runs = pd.DataFrame(
        [
            {
                "run_id": 3,
                "status": "FAILED",
                "successful_repositories": 0,
                "error_details": "404 not found",
            },
            {
                "run_id": 2,
                "status": "COMPLETED",
                "successful_repositories": 3,
                "error_details": None,
            },
        ]
    )

    with patch("src.dashboard.st") as mock_st:
        mock_st.columns.return_value = [MagicMock(), MagicMock()]
        render_header(failed_runs)

        calls = mock_st.html.call_args_list if mock_st.html.called else mock_st.markdown.call_args_list
        rendered_texts = [str(c[0][0]) for c in calls]
        combined = " ".join(rendered_texts)
        assert "Latest Run #3: FAILED" in combined
        assert "badge-pill-danger" in combined
        assert "Last Successful: Run #2" in combined
