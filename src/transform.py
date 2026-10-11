"""GitPulse Transformation and Analytics Module (Milestone 3).

Reads repository metadata and time-series snapshots from the SQLite database,
computes quality-controlled analytics using pandas and SQL, and exports structured
CSV files to data/processed/.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure GitPulse project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

try:
    from src.database import DEFAULT_DB_PATH, DatabaseManager
except ModuleNotFoundError:
    from database import DEFAULT_DB_PATH, DatabaseManager

logger = logging.getLogger("gitpulse.transform")

DEFAULT_PROCESSED_DIR = Path("data/processed")


class GitPulseTransformer:
    """Performs read-only analytical transformations on GitPulse SQLite data."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        db_path: str | Path = DEFAULT_DB_PATH,
        output_dir: str | Path = DEFAULT_PROCESSED_DIR,
    ):
        if db_manager is not None:
            self.db = db_manager
        else:
            self.db = DatabaseManager(db_path=db_path)
        self.output_dir = Path(output_dir)

    def load_raw_repositories(self) -> pd.DataFrame:
        """Load repositories table from SQLite into a cleaned pandas DataFrame."""
        with self.db.get_connection() as conn:
            query = """
                SELECT
                    repo_id,
                    owner,
                    name,
                    description,
                    primary_language,
                    stars,
                    forks,
                    open_issues,
                    creation_date,
                    last_updated_date,
                    url,
                    ingested_at
                FROM repositories
            """
            df = pd.read_sql_query(query, conn)

        if df.empty:
            return pd.DataFrame(
                columns=[
                    "repo_id",
                    "owner",
                    "name",
                    "full_name",
                    "description",
                    "primary_language",
                    "stars",
                    "forks",
                    "open_issues",
                    "creation_date",
                    "last_updated_date",
                    "url",
                    "ingested_at",
                ]
            )

        # Data cleaning and type enforcement
        df["repo_id"] = pd.to_numeric(df["repo_id"], errors="coerce").fillna(0).astype(int)
        df["stars"] = pd.to_numeric(df["stars"], errors="coerce").fillna(0).astype(int)
        df["forks"] = pd.to_numeric(df["forks"], errors="coerce").fillna(0).astype(int)
        df["open_issues"] = pd.to_numeric(df["open_issues"], errors="coerce").fillna(0).astype(int)

        # Normalize text fields and languages
        df["owner"] = df["owner"].fillna("").astype(str).str.strip()
        df["name"] = df["name"].fillna("").astype(str).str.strip()
        df["full_name"] = df["owner"] + "/" + df["name"]
        df["description"] = df["description"].fillna("").astype(str).str.strip()

        # Handle missing, None, or 'None' languages gracefully
        lang_clean = df["primary_language"].fillna("Unknown").astype(str).str.strip()
        df["primary_language"] = lang_clean.replace({"None": "Unknown", "": "Unknown", "null": "Unknown"})

        return df

    def load_raw_snapshots(self) -> pd.DataFrame:
        """Load repository_snapshots table from SQLite into a cleaned pandas DataFrame."""
        with self.db.get_connection() as conn:
            query = """
                SELECT
                    id,
                    repo_id,
                    collection_timestamp,
                    stars,
                    forks,
                    open_issues
                FROM repository_snapshots
                ORDER BY collection_timestamp ASC, id ASC
            """
            df = pd.read_sql_query(query, conn)

        if df.empty:
            return pd.DataFrame(
                columns=[
                    "id",
                    "repo_id",
                    "collection_timestamp",
                    "stars",
                    "forks",
                    "open_issues",
                ]
            )

        df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
        df["repo_id"] = pd.to_numeric(df["repo_id"], errors="coerce").fillna(0).astype(int)
        df["stars"] = pd.to_numeric(df["stars"], errors="coerce").fillna(0).astype(int)
        df["forks"] = pd.to_numeric(df["forks"], errors="coerce").fillna(0).astype(int)
        df["open_issues"] = pd.to_numeric(df["open_issues"], errors="coerce").fillna(0).astype(int)
        return df

    def compute_top_repositories(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Compute repositories ranked by stars and forks.

        Args:
            limit: Optional maximum number of repositories to return.

        Returns:
            DataFrame with star rank, fork rank, and repository metadata.
        """
        df = self.load_raw_repositories()
        if df.empty:
            cols = [
                "star_rank",
                "fork_rank",
                "repo_id",
                "full_name",
                "primary_language",
                "stars",
                "forks",
                "open_issues",
                "url",
            ]
            return pd.DataFrame(columns=cols)

        # Star rank (1 = most stars)
        df["star_rank"] = df["stars"].rank(ascending=False, method="min").astype(int)
        # Fork rank (1 = most forks)
        df["fork_rank"] = df["forks"].rank(ascending=False, method="min").astype(int)

        # Sort primarily by stars descending, secondarily by forks descending
        result = df.sort_values(by=["stars", "forks"], ascending=[False, False])
        if limit is not None and limit > 0:
            result = result.head(limit)

        ordered_cols = [
            "star_rank",
            "fork_rank",
            "repo_id",
            "full_name",
            "primary_language",
            "stars",
            "forks",
            "open_issues",
            "url",
        ]
        return result[ordered_cols].reset_index(drop=True)

    def compute_language_summary(self) -> pd.DataFrame:
        """Group repositories by programming language, computing counts and aggregated stars/forks.

        Handles unknown or missing languages safely as 'Unknown'.

        Returns:
            DataFrame with language metrics sorted by total stars descending.
        """
        df = self.load_raw_repositories()
        if df.empty:
            cols = [
                "primary_language",
                "repo_count",
                "total_stars",
                "avg_stars",
                "total_forks",
                "avg_forks",
                "total_open_issues",
            ]
            return pd.DataFrame(columns=cols)

        grouped = (
            df.groupby("primary_language", as_index=False)
            .agg(
                repo_count=("repo_id", "count"),
                total_stars=("stars", "sum"),
                avg_stars=("stars", "mean"),
                total_forks=("forks", "sum"),
                avg_forks=("forks", "mean"),
                total_open_issues=("open_issues", "sum"),
            )
        )

        # Round averages for clean reporting
        grouped["avg_stars"] = grouped["avg_stars"].round(1)
        grouped["avg_forks"] = grouped["avg_forks"].round(1)

        # Sort by total_stars descending, then repo_count descending
        result = grouped.sort_values(
            by=["total_stars", "repo_count"], ascending=[False, False]
        ).reset_index(drop=True)

        return result

    def compute_repository_activity(self) -> pd.DataFrame:
        """Compute repository age, update latency, and issue/star health ratios.

        Returns:
            DataFrame with activity and age metrics.
        """
        df = self.load_raw_repositories()
        cols = [
            "repo_id",
            "full_name",
            "primary_language",
            "stars",
            "forks",
            "open_issues",
            "creation_date",
            "last_updated_date",
            "age_days",
            "days_since_update",
            "issue_to_star_pct",
            "fork_to_star_pct",
        ]
        if df.empty:
            return pd.DataFrame(columns=cols)

        now_utc = datetime.now(timezone.utc)

        # Parse datetime columns safely
        created_dt = pd.to_datetime(df["creation_date"], errors="coerce", utc=True)
        updated_dt = pd.to_datetime(df["last_updated_date"], errors="coerce", utc=True)

        # Compute age in days
        df["age_days"] = ((now_utc - created_dt).dt.total_seconds() / 86400.0).round(1)
        df["days_since_update"] = ((now_utc - updated_dt).dt.total_seconds() / 86400.0).round(1)

        # Fill NaNs where dates were missing
        df["age_days"] = df["age_days"].fillna(-1.0)
        df["days_since_update"] = df["days_since_update"].fillna(-1.0)

        # Issue-to-star ratio (percentage)
        # Avoid division by zero
        stars_nonzero = df["stars"].replace(0, np.nan)
        df["issue_to_star_pct"] = ((df["open_issues"] / stars_nonzero) * 100).round(2).fillna(0.0)
        df["fork_to_star_pct"] = ((df["forks"] / stars_nonzero) * 100).round(2).fillna(0.0)

        result = df.sort_values(by="open_issues", ascending=False).reset_index(drop=True)
        return result[cols]

    def compute_historical_changes(self) -> pd.DataFrame:
        """Calculate historical metric changes across snapshots for each repository.

        If a repository has only 1 snapshot, explicitly marks that historical
        changes cannot yet be calculated.

        Returns:
            DataFrame summarizing earliest vs latest snapshots and metric deltas.
        """
        repos_df = self.load_raw_repositories()
        snaps_df = self.load_raw_snapshots()

        cols = [
            "repo_id",
            "full_name",
            "snapshot_count",
            "status",
            "first_snapshot_time",
            "latest_snapshot_time",
            "initial_stars",
            "latest_stars",
            "delta_stars",
            "initial_forks",
            "latest_forks",
            "delta_forks",
            "initial_open_issues",
            "latest_open_issues",
            "delta_open_issues",
            "notes",
        ]

        if repos_df.empty:
            return pd.DataFrame(columns=cols)

        records: List[Dict[str, Any]] = []

        for _, repo_row in repos_df.iterrows():
            r_id = int(repo_row["repo_id"])
            full_name = repo_row["full_name"]
            repo_snaps = snaps_df[snaps_df["repo_id"] == r_id].sort_values(
                by=["collection_timestamp", "id"], ascending=[True, True]
            )

            count = len(repo_snaps)

            if count == 0:
                records.append(
                    {
                        "repo_id": r_id,
                        "full_name": full_name,
                        "snapshot_count": 0,
                        "status": "NO_SNAPSHOTS",
                        "first_snapshot_time": None,
                        "latest_snapshot_time": None,
                        "initial_stars": None,
                        "latest_stars": None,
                        "delta_stars": None,
                        "initial_forks": None,
                        "latest_forks": None,
                        "delta_forks": None,
                        "initial_open_issues": None,
                        "latest_open_issues": None,
                        "delta_open_issues": None,
                        "notes": "No historical snapshots exist for this repository.",
                    }
                )
            elif count == 1:
                first_snap = repo_snaps.iloc[0]
                records.append(
                    {
                        "repo_id": r_id,
                        "full_name": full_name,
                        "snapshot_count": 1,
                        "status": "INSUFFICIENT_DATA",
                        "first_snapshot_time": first_snap["collection_timestamp"],
                        "latest_snapshot_time": first_snap["collection_timestamp"],
                        "initial_stars": int(first_snap["stars"]),
                        "latest_stars": int(first_snap["stars"]),
                        "delta_stars": None,
                        "initial_forks": int(first_snap["forks"]),
                        "latest_forks": int(first_snap["forks"]),
                        "delta_forks": None,
                        "initial_open_issues": int(first_snap["open_issues"]),
                        "latest_open_issues": int(first_snap["open_issues"]),
                        "delta_open_issues": None,
                        "notes": "Historical change cannot yet be calculated (only 1 snapshot recorded).",
                    }
                )
            else:
                first_snap = repo_snaps.iloc[0]
                latest_snap = repo_snaps.iloc[-1]

                d_stars = int(latest_snap["stars"]) - int(first_snap["stars"])
                d_forks = int(latest_snap["forks"]) - int(first_snap["forks"])
                d_issues = int(latest_snap["open_issues"]) - int(first_snap["open_issues"])

                records.append(
                    {
                        "repo_id": r_id,
                        "full_name": full_name,
                        "snapshot_count": count,
                        "status": "CALCULATED",
                        "first_snapshot_time": first_snap["collection_timestamp"],
                        "latest_snapshot_time": latest_snap["collection_timestamp"],
                        "initial_stars": int(first_snap["stars"]),
                        "latest_stars": int(latest_snap["stars"]),
                        "delta_stars": d_stars,
                        "initial_forks": int(first_snap["forks"]),
                        "latest_forks": int(latest_snap["forks"]),
                        "delta_forks": d_forks,
                        "initial_open_issues": int(first_snap["open_issues"]),
                        "latest_open_issues": int(latest_snap["open_issues"]),
                        "delta_open_issues": d_issues,
                        "notes": f"Calculated across {count} snapshots.",
                    }
                )

        result_df = pd.DataFrame(records, columns=cols)
        return result_df

    def export_all(self) -> Dict[str, Path]:
        """Compute all analytical views and export them to CSV files in data/processed/.

        Returns:
            Dictionary mapping analytical name to exported file Path.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)

        top_repos_df = self.compute_top_repositories()
        lang_df = self.compute_language_summary()
        activity_df = self.compute_repository_activity()
        history_df = self.compute_historical_changes()

        export_paths: Dict[str, Path] = {
            "top_repositories": self.output_dir / "top_repositories.csv",
            "language_summary": self.output_dir / "language_summary.csv",
            "repository_activity": self.output_dir / "repository_activity.csv",
            "historical_changes": self.output_dir / "historical_changes.csv",
        }

        top_repos_df.to_csv(export_paths["top_repositories"], index=False, encoding="utf-8")
        lang_df.to_csv(export_paths["language_summary"], index=False, encoding="utf-8")
        activity_df.to_csv(export_paths["repository_activity"], index=False, encoding="utf-8")
        history_df.to_csv(export_paths["historical_changes"], index=False, encoding="utf-8")

        for key, path in export_paths.items():
            logger.info("Exported %s to %s", key, path)

        return export_paths


def main():
    """CLI execution for Milestone 3 transformation pipeline."""
    parser = argparse.ArgumentParser(
        description="GitPulse Transformation and Analytics Module (Milestone 3)"
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to SQLite database (default: data/gitpulse.db)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_PROCESSED_DIR),
        help="Directory to export processed CSV files (default: data/processed)",
    )

    args = parser.parse_args()
    transformer = GitPulseTransformer(db_path=args.db_path, output_dir=args.output_dir)

    print("=" * 65)
    print("GitPulse Transformation & Analytics Pipeline (Milestone 3)")
    print(f"Database Source: {args.db_path}")
    print(f"Export Target:   {args.output_dir}")
    print("=" * 65)

    # Compute views
    top_repos = transformer.compute_top_repositories()
    lang_summary = transformer.compute_language_summary()
    activity = transformer.compute_repository_activity()
    history = transformer.compute_historical_changes()
    exported = transformer.export_all()

    # Pretty-print Summary
    print(f"\n1. TOP REPOSITORIES BY STARS ({len(top_repos)} total):")
    if not top_repos.empty:
        print(
            top_repos[
                ["star_rank", "fork_rank", "full_name", "primary_language", "stars", "forks"]
            ].to_string(index=False)
        )
    else:
        print("  (No repositories found in database)")

    print(f"\n2. LANGUAGE SUMMARY ({len(lang_summary)} languages):")
    if not lang_summary.empty:
        print(
            lang_summary[
                ["primary_language", "repo_count", "total_stars", "avg_stars", "total_forks"]
            ].to_string(index=False)
        )
    else:
        print("  (No language data found)")

    print(f"\n3. REPOSITORY ACTIVITY & ISSUES ({len(activity)} total):")
    if not activity.empty:
        print(
            activity[
                ["full_name", "open_issues", "age_days", "days_since_update", "issue_to_star_pct"]
            ].to_string(index=False)
        )
    else:
        print("  (No activity data found)")

    print(f"\n4. HISTORICAL CHANGES ACROSS SNAPSHOTS ({len(history)} total):")
    if not history.empty:
        print(
            history[
                [
                    "full_name",
                    "snapshot_count",
                    "status",
                    "initial_stars",
                    "latest_stars",
                    "delta_stars",
                    "notes",
                ]
            ].to_string(index=False)
        )
    else:
        print("  (No snapshot history found)")

    print("\n" + "=" * 65)
    print("EXPORTED CSV FILES:")
    for name, path in exported.items():
        print(f"  - {name:20}: {path}")
    print("=" * 65)


if __name__ == "__main__":
    main()
