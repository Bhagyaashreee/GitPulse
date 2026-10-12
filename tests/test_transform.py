"""Unit and integration tests for GitPulse Data Transformation and Analytics (Milestone 3).

Uses isolated temporary SQLite databases to ensure deterministic tests and prevent
mutating the project database.
"""

from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import pytest

from src.database import DatabaseManager
from src.transform import GitPulseTransformer


@pytest.fixture
def test_db_manager(tmp_path):
    """Fixture providing an isolated DatabaseManager."""
    db_file = tmp_path / "test_transform.db"
    return DatabaseManager(db_path=db_file)


@pytest.fixture
def populated_transformer(test_db_manager, tmp_path):
    """Fixture with a populated database containing diverse deterministic repo data."""
    # Insert Repositories
    repos = [
        {
            "repo_id": 101,
            "owner": "org-a",
            "name": "fast-tool",
            "description": "High performance tool",
            "primary_language": "Rust",
            "stars": 8000,
            "forks": 400,
            "open_issues": 25,
            "creation_date": "2020-01-01T00:00:00Z",
            "last_updated_date": "2026-10-01T00:00:00Z",
            "url": "https://github.com/org-a/fast-tool",
        },
        {
            "repo_id": 102,
            "owner": "org-b",
            "name": "web-framework",
            "description": "Python web framework",
            "primary_language": "Python",
            "stars": 12000,
            "forks": 1500,
            "open_issues": 120,
            "creation_date": "2018-05-15T12:00:00Z",
            "last_updated_date": "2026-10-10T12:00:00Z",
            "url": "https://github.com/org-b/web-framework",
        },
        {
            "repo_id": 103,
            "owner": "org-b",
            "name": "data-lib",
            "description": "Python analytics library",
            "primary_language": "Python",
            "stars": 5000,
            "forks": 800,
            "open_issues": 40,
            "creation_date": "2021-03-10T08:00:00Z",
            "last_updated_date": "2026-09-20T10:00:00Z",
            "url": "https://github.com/org-b/data-lib",
        },
        {
            "repo_id": 104,
            "owner": "org-c",
            "name": "awesome-list",
            "description": "Curated list with no code",
            "primary_language": None,  # Missing language
            "stars": 3000,
            "forks": 200,
            "open_issues": 5,
            "creation_date": "2022-07-01T00:00:00Z",
            "last_updated_date": "2026-08-01T00:00:00Z",
            "url": "https://github.com/org-c/awesome-list",
        },
    ]

    for r in repos:
        test_db_manager.upsert_repository(r)

    # Insert Historical Snapshots
    # Repo 101: 3 snapshots (Calculated)
    test_db_manager.insert_snapshot(
        repo_id=101, stars=7500, forks=380, open_issues=30, collection_timestamp="2026-10-01T10:00:00Z"
    )
    test_db_manager.insert_snapshot(
        repo_id=101, stars=7800, forks=390, open_issues=28, collection_timestamp="2026-10-05T10:00:00Z"
    )
    test_db_manager.insert_snapshot(
        repo_id=101, stars=8000, forks=400, open_issues=25, collection_timestamp="2026-10-10T10:00:00Z"
    )

    # Repo 102: 1 snapshot (Insufficient data)
    test_db_manager.insert_snapshot(
        repo_id=102, stars=12000, forks=1500, open_issues=120, collection_timestamp="2026-10-10T10:00:00Z"
    )

    # Repo 103: 0 snapshots (No snapshots)
    # Repo 104: 2 snapshots (Calculated with 0 delta)
    test_db_manager.insert_snapshot(
        repo_id=104, stars=3000, forks=200, open_issues=5, collection_timestamp="2026-10-09T00:00:00Z"
    )
    test_db_manager.insert_snapshot(
        repo_id=104, stars=3000, forks=200, open_issues=5, collection_timestamp="2026-10-10T00:00:00Z"
    )

    output_dir = tmp_path / "processed"
    return GitPulseTransformer(db_manager=test_db_manager, output_dir=output_dir)


def test_empty_database_handling(test_db_manager, tmp_path):
    """Test that all transformation functions handle an empty database gracefully."""
    transformer = GitPulseTransformer(db_manager=test_db_manager, output_dir=tmp_path / "proc")

    top_repos = transformer.compute_top_repositories()
    assert top_repos.empty
    assert "star_rank" in top_repos.columns

    lang_summary = transformer.compute_language_summary()
    assert lang_summary.empty
    assert "primary_language" in lang_summary.columns

    activity = transformer.compute_repository_activity()
    assert activity.empty
    assert "age_days" in activity.columns

    history = transformer.compute_historical_changes()
    assert history.empty
    assert "delta_stars" in history.columns

    # Verify export doesn't fail on empty database
    exported = transformer.export_all()
    assert len(exported) == 4
    for p in exported.values():
        assert p.exists()


def test_rankings_by_stars_and_forks(populated_transformer):
    """Test ranking calculation for stars and forks."""
    top_repos = populated_transformer.compute_top_repositories()
    assert len(top_repos) == 4

    # Stars order: 102 (12,000), 101 (8,000), 103 (5,000), 104 (3,000)
    assert top_repos.iloc[0]["repo_id"] == 102
    assert top_repos.iloc[0]["star_rank"] == 1
    assert top_repos.iloc[0]["fork_rank"] == 1  # 1500 forks is also #1

    assert top_repos.iloc[1]["repo_id"] == 101
    assert top_repos.iloc[1]["star_rank"] == 2
    assert top_repos.iloc[1]["fork_rank"] == 3  # 400 forks is rank 3

    # Test limit parameter
    top_2 = populated_transformer.compute_top_repositories(limit=2)
    assert len(top_2) == 2
    assert top_2.iloc[0]["repo_id"] == 102
    assert top_2.iloc[1]["repo_id"] == 101


def test_language_summary_and_aggregation(populated_transformer):
    """Test language grouping, counts, total stars, and average stars."""
    lang_df = populated_transformer.compute_language_summary()

    # Languages present: Python (2 repos), Rust (1 repo), Unknown (1 repo)
    assert len(lang_df) == 3

    python_row = lang_df[lang_df["primary_language"] == "Python"].iloc[0]
    assert python_row["repo_count"] == 2
    assert python_row["total_stars"] == 17000  # 12000 + 5000
    assert python_row["avg_stars"] == 8500.0
    assert python_row["total_forks"] == 2300  # 1500 + 800

    rust_row = lang_df[lang_df["primary_language"] == "Rust"].iloc[0]
    assert rust_row["repo_count"] == 1
    assert rust_row["total_stars"] == 8000

    unknown_row = lang_df[lang_df["primary_language"] == "Unknown"].iloc[0]
    assert unknown_row["repo_count"] == 1
    assert unknown_row["total_stars"] == 3000


def test_data_quality_missing_and_invalid_values(test_db_manager, tmp_path):
    """Test that nulls, empty strings, and missing attributes are safely imputed."""
    sparse_repo = {
        "repo_id": 999,
        "owner": "sparse-owner",
        "name": "sparse-repo",
        "description": None,
        "primary_language": "",  # Empty string language
        "stars": None,  # None stars
        "forks": None,
        "open_issues": None,
        "creation_date": "invalid-date",
        "last_updated_date": None,
        "url": "https://github.com/sparse-owner/sparse-repo",
    }
    test_db_manager.upsert_repository(sparse_repo)

    transformer = GitPulseTransformer(db_manager=test_db_manager, output_dir=tmp_path / "proc")
    repos_df = transformer.load_raw_repositories()

    row = repos_df.iloc[0]
    assert row["primary_language"] == "Unknown"
    assert row["stars"] == 0
    assert row["forks"] == 0
    assert row["open_issues"] == 0

    activity = transformer.compute_repository_activity()
    act_row = activity.iloc[0]
    assert act_row["age_days"] == -1.0  # Invalid date fallback
    assert act_row["days_since_update"] == -1.0  # None date fallback
    assert act_row["issue_to_star_pct"] == 0.0  # 0 division protection


def test_historical_changes_multi_snapshot(populated_transformer):
    """Test historical change calculations for repository with multiple snapshots."""
    history = populated_transformer.compute_historical_changes()

    row_101 = history[history["repo_id"] == 101].iloc[0]
    assert row_101["snapshot_count"] == 3
    assert row_101["status"] == "CALCULATED"
    assert row_101["initial_stars"] == 7500
    assert row_101["latest_stars"] == 8000
    assert row_101["delta_stars"] == 500  # 8000 - 7500
    assert row_101["delta_forks"] == 20  # 400 - 380
    assert row_101["delta_open_issues"] == -5  # 25 - 30


def test_historical_changes_single_snapshot(populated_transformer):
    """Test that a repository with only one snapshot is explicitly reported as insufficient."""
    history = populated_transformer.compute_historical_changes()

    row_102 = history[history["repo_id"] == 102].iloc[0]
    assert row_102["snapshot_count"] == 1
    assert row_102["status"] == "INSUFFICIENT_DATA"
    assert pd.isna(row_102["delta_stars"])
    assert pd.isna(row_102["delta_forks"])
    assert "only 1 snapshot recorded" in row_102["notes"]


def test_historical_changes_zero_snapshots(populated_transformer):
    """Test repository with zero snapshots."""
    history = populated_transformer.compute_historical_changes()

    row_103 = history[history["repo_id"] == 103].iloc[0]
    assert row_103["snapshot_count"] == 0
    assert row_103["status"] == "NO_SNAPSHOTS"
    assert pd.isna(row_103["delta_stars"])


def test_repository_activity_ratios(populated_transformer):
    """Test age, update latency, and issue-to-star ratio calculations."""
    activity = populated_transformer.compute_repository_activity()
    assert len(activity) == 4

    row_102 = activity[activity["repo_id"] == 102].iloc[0]
    assert row_102["age_days"] > 0
    assert row_102["days_since_update"] >= 0
    # 120 issues / 12000 stars = 1.0%
    assert row_102["issue_to_star_pct"] == 1.0


def test_export_all_csv_files(populated_transformer, tmp_path):
    """Test exporting all analytical views to CSV and verify file integrity."""
    exported = populated_transformer.export_all()

    for name in ["top_repositories", "language_summary", "repository_activity", "historical_changes"]:
        file_path = exported[name]
        assert file_path.exists()
        assert file_path.stat().st_size > 0

        # Verify readability with pandas
        loaded_df = pd.read_csv(file_path)
        assert not loaded_df.empty


def test_snapshot_history_three_snapshots_incremental_deltas(populated_transformer):
    """Test consecutive snapshot deltas, negative issue deltas, and star velocity across 3 snapshots."""
    # Repo 101 has 3 snapshots in populated_transformer:
    # 1: 2026-10-01T10:00:00Z -> stars: 7500, forks: 380, issues: 30
    # 2: 2026-10-05T10:00:00Z -> stars: 7800, forks: 390, issues: 28
    # 3: 2026-10-10T10:00:00Z -> stars: 8000, forks: 400, issues: 25
    history_df = populated_transformer.compute_repository_snapshot_history(101)

    assert len(history_df) == 3

    # Baseline snapshot (0)
    snap0 = history_df.iloc[0]
    assert snap0["stars"] == 7500
    assert snap0["delta_stars"] == 0
    assert snap0["delta_forks"] == 0
    assert snap0["delta_open_issues"] == 0
    assert snap0["cumulative_delta_stars"] == 0
    assert snap0["elapsed_seconds"] == 0.0
    assert snap0["star_velocity_per_day"] == 0.0

    # Snapshot 1 (4 days elapsed)
    snap1 = history_df.iloc[1]
    assert snap1["stars"] == 7800
    assert snap1["delta_stars"] == 300  # 7800 - 7500
    assert snap1["delta_forks"] == 10   # 390 - 380
    assert snap1["delta_open_issues"] == -2  # 28 - 30 (negative issue delta!)
    assert snap1["cumulative_delta_stars"] == 300
    assert snap1["elapsed_seconds"] == 4 * 86400.0
    assert snap1["star_velocity_per_day"] == 75.0  # 300 stars / 4 days

    # Snapshot 2 (5 days elapsed)
    snap2 = history_df.iloc[2]
    assert snap2["stars"] == 8000
    assert snap2["delta_stars"] == 200  # 8000 - 7800
    assert snap2["delta_forks"] == 10   # 400 - 390
    assert snap2["delta_open_issues"] == -3  # 25 - 28 (negative issue delta!)
    assert snap2["cumulative_delta_stars"] == 500  # 8000 - 7500
    assert snap2["cumulative_delta_forks"] == 20   # 400 - 380
    assert snap2["cumulative_delta_open_issues"] == -5  # 25 - 30
    assert snap2["elapsed_seconds"] == 5 * 86400.0
    assert snap2["star_velocity_per_day"] == 40.0  # 200 stars / 5 days


def test_snapshot_history_unchanged_metrics_and_zero_elapsed_time(test_db_manager, tmp_path):
    """Test safe handling of unchanged metrics and zero elapsed time between snapshots."""
    test_db_manager.upsert_repository(
        {
            "repo_id": 201,
            "owner": "test",
            "name": "zero-time-repo",
            "description": "Test zero elapsed time",
            "primary_language": "Python",
            "stars": 500,
            "forks": 50,
            "open_issues": 10,
            "creation_date": "2022-01-01T00:00:00Z",
            "last_updated_date": "2026-10-10T00:00:00Z",
            "url": "https://github.com/test/zero-time-repo",
        }
    )

    # Insert two snapshots with the identical timestamp and unchanged metrics
    test_db_manager.insert_snapshot(
        repo_id=201, stars=500, forks=50, open_issues=10, collection_timestamp="2026-10-10T12:00:00Z"
    )
    test_db_manager.insert_snapshot(
        repo_id=201, stars=500, forks=50, open_issues=10, collection_timestamp="2026-10-10T12:00:00Z"
    )

    transformer = GitPulseTransformer(db_manager=test_db_manager, output_dir=tmp_path / "proc")
    history_df = transformer.compute_repository_snapshot_history(201)

    assert len(history_df) == 2
    snap1 = history_df.iloc[1]
    assert snap1["delta_stars"] == 0
    assert snap1["delta_forks"] == 0
    assert snap1["delta_open_issues"] == 0
    assert snap1["elapsed_seconds"] == 0.0
    # Division by zero avoided
    assert snap1["star_velocity_per_day"] == 0.0


def test_snapshot_history_single_and_zero_snapshot_fallbacks(populated_transformer):
    """Test fallbacks when repository has 0 or 1 snapshot."""
    # Repo 103 has 0 snapshots
    df_0 = populated_transformer.compute_repository_snapshot_history(103)
    assert df_0.empty
    assert "delta_stars" in df_0.columns
    assert "star_velocity_per_day" in df_0.columns

    # Repo 102 has 1 snapshot
    df_1 = populated_transformer.compute_repository_snapshot_history(102)
    assert len(df_1) == 1
    assert df_1.iloc[0]["delta_stars"] == 0
    assert df_1.iloc[0]["delta_forks"] == 0
    assert df_1.iloc[0]["delta_open_issues"] == 0
    assert df_1.iloc[0]["elapsed_seconds"] == 0.0
    assert df_1.iloc[0]["star_velocity_per_day"] == 0.0
