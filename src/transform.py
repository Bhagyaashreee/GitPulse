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

    def compute_repository_snapshot_history(
        self,
        repo_id: int,
        snapshots_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """Calculate chronological consecutive snapshot deltas and growth velocity for a repository.

        Computes incremental deltas for stars, forks, and open issues between consecutive
        snapshots, as well as star growth velocity (stars/day) based on actual elapsed time.
        Safely handles zero elapsed time, single snapshots, or repositories without snapshots.

        Args:
            repo_id: The integer ID of the repository.
            snapshots_df: Optional preloaded snapshots DataFrame. If None, loaded via load_raw_snapshots().

        Returns:
            DataFrame with chronological snapshot progression, step deltas, cumulative deltas,
            elapsed seconds, and star velocity.
        """
        cols = [
            "snapshot_id",
            "repo_id",
            "collection_timestamp",
            "stars",
            "forks",
            "open_issues",
            "delta_stars",
            "delta_forks",
            "delta_open_issues",
            "cumulative_delta_stars",
            "cumulative_delta_forks",
            "cumulative_delta_open_issues",
            "elapsed_seconds",
            "star_velocity_per_day",
        ]

        if snapshots_df is not None:
            snaps_df = snapshots_df.copy()
        else:
            snaps_df = self.load_raw_snapshots()

        if snaps_df.empty:
            return pd.DataFrame(columns=cols)

        # Filter by repo_id and sort chronologically
        repo_snaps = snaps_df[snaps_df["repo_id"] == int(repo_id)].sort_values(
            by=["collection_timestamp", "id"], ascending=[True, True]
        ).reset_index(drop=True)

        if repo_snaps.empty:
            return pd.DataFrame(columns=cols)

        records: List[Dict[str, Any]] = []
        base_snap = repo_snaps.iloc[0]

        for i, snap in repo_snaps.iterrows():
            s_id = int(snap["id"])
            c_ts = str(snap["collection_timestamp"])
            stars = int(snap["stars"])
            forks = int(snap["forks"])
            issues = int(snap["open_issues"])

            if i == 0:
                delta_stars = 0
                delta_forks = 0
                delta_issues = 0
                cum_stars = 0
                cum_forks = 0
                cum_issues = 0
                elapsed_sec = 0.0
                velocity_per_day = 0.0
            else:
                prev = repo_snaps.iloc[i - 1]
                delta_stars = stars - int(prev["stars"])
                delta_forks = forks - int(prev["forks"])
                delta_issues = issues - int(prev["open_issues"])

                cum_stars = stars - int(base_snap["stars"])
                cum_forks = forks - int(base_snap["forks"])
                cum_issues = issues - int(base_snap["open_issues"])

                # Calculate actual elapsed time between consecutive snapshots
                t_prev = pd.to_datetime(prev["collection_timestamp"], errors="coerce", utc=True)
                t_curr = pd.to_datetime(snap["collection_timestamp"], errors="coerce", utc=True)

                if pd.notna(t_prev) and pd.notna(t_curr):
                    elapsed_sec = max(0.0, float((t_curr - t_prev).total_seconds()))
                else:
                    elapsed_sec = 0.0

                # Safe velocity calculation: prevent division by zero or negative intervals
                if elapsed_sec > 0.0:
                    elapsed_days = elapsed_sec / 86400.0
                    velocity_per_day = round(float(delta_stars) / elapsed_days, 2)
                else:
                    velocity_per_day = 0.0

            records.append(
                {
                    "snapshot_id": s_id,
                    "repo_id": int(repo_id),
                    "collection_timestamp": c_ts,
                    "stars": stars,
                    "forks": forks,
                    "open_issues": issues,
                    "delta_stars": delta_stars,
                    "delta_forks": delta_forks,
                    "delta_open_issues": delta_issues,
                    "cumulative_delta_stars": cum_stars,
                    "cumulative_delta_forks": cum_forks,
                    "cumulative_delta_open_issues": cum_issues,
                    "elapsed_seconds": round(elapsed_sec, 1),
                    "star_velocity_per_day": velocity_per_day,
                }
            )

        return pd.DataFrame(records, columns=cols)

    def compute_repository_health_scores(self) -> pd.DataFrame:
        """Calculate explainable heuristic repository health scores (0-100) using observable signals.

        Evaluates five transparent heuristic pillars:
        1. Maintenance Recency (Max 35 pts): Days since last update. Excluded if update date missing.
        2. Backlog Pressure (Max 25 pts): Open issues & PRs relative to community stars.
           Note: GitHub's open_issues_count includes both open issues and pull requests combined.
        3. Community Adoption (Max 20 pts): Forks relative to stars.
        4. Project Hygiene (Max 10 pts): Presence of description and primary language.
        5. Observed Snapshot Trends (Max 10 pts): Star growth and issue burn-down across collection cycles.
           Only evaluated when >= 2 snapshots exist. Excluded from denominator if < 2 snapshots.

        Uses dynamic available-weight normalization:
            health_score = round((points_earned / available_weight) * 100)
            data_coverage_pct = round(available_weight)
        If available_weight < 40, health_score is None and grade is 'N/A' (Insufficient Signals).

        Returns:
            DataFrame with component-level scores, statuses, explanations, total health score, and grade.
        """
        raw_repos_df = self.load_raw_repositories()
        act_df = self.compute_repository_activity()
        hist_df = self.compute_historical_changes()

        cols = [
            "repo_id",
            "full_name",
            "health_score",
            "health_grade",
            "data_coverage_pct",
            "evaluated_weight",
            "points_earned",
            "recency_score",
            "recency_max",
            "recency_status",
            "recency_detail",
            "backlog_score",
            "backlog_max",
            "backlog_status",
            "backlog_detail",
            "community_score",
            "community_max",
            "community_status",
            "community_detail",
            "hygiene_score",
            "hygiene_max",
            "hygiene_status",
            "hygiene_detail",
            "trend_score",
            "trend_max",
            "trend_status",
            "trend_detail",
            "notes",
        ]

        if raw_repos_df.empty:
            return pd.DataFrame(columns=cols)

        # Build lookup maps for activity and history metrics
        act_map = {row["repo_id"]: row for _, row in act_df.iterrows()} if not act_df.empty else {}
        hist_map = {row["repo_id"]: row for _, row in hist_df.iterrows()} if not hist_df.empty else {}

        records: List[Dict[str, Any]] = []

        for _, repo in raw_repos_df.iterrows():
            r_id = int(repo["repo_id"])
            full_name = str(repo["full_name"])
            stars = int(repo["stars"])
            forks = int(repo["forks"])
            open_issues = int(repo["open_issues"])
            desc = str(repo.get("description") or "").strip()
            lang = str(repo.get("primary_language") or "").strip()

            act_row = act_map.get(r_id, {})
            days_since_update = float(act_row.get("days_since_update", -1.0))

            hist_row = hist_map.get(r_id, {})
            snap_count = int(hist_row.get("snapshot_count", 0))
            hist_status = str(hist_row.get("status", "NO_SNAPSHOTS"))
            delta_stars = hist_row.get("delta_stars")
            delta_issues = hist_row.get("delta_open_issues")

            earned_pts = 0.0
            avail_weight = 0.0

            # 1. Maintenance Recency (Max 35)
            if days_since_update < 0.0:
                rec_score = None
                rec_max = 0.0
                rec_status = "DATA_UNAVAILABLE"
                rec_detail = "Last update timestamp missing from repository metadata"
            else:
                rec_max = 35.0
                avail_weight += 35.0
                rec_status = "AVAILABLE"
                if days_since_update <= 14.0:
                    rec_score = 35.0
                    rec_detail = f"Updated {days_since_update:.1f} days ago (very active)"
                elif days_since_update <= 45.0:
                    rec_score = 30.0
                    rec_detail = f"Updated {days_since_update:.1f} days ago (active maintenance)"
                elif days_since_update <= 90.0:
                    rec_score = 22.0
                    rec_detail = f"Updated {days_since_update:.1f} days ago (regular maintenance)"
                elif days_since_update <= 180.0:
                    rec_score = 15.0
                    rec_detail = f"Updated {days_since_update:.1f} days ago (moderate activity)"
                elif days_since_update <= 365.0:
                    rec_score = 8.0
                    rec_detail = f"Updated {days_since_update:.1f} days ago (slow maintenance)"
                else:
                    rec_score = 0.0
                    rec_detail = f"Updated {days_since_update:.1f} days ago (stale / inactive > 1 year)"
                earned_pts += rec_score

            # 2. Backlog Pressure (Issues & PRs) (Max 25)
            bkg_max = 25.0
            avail_weight += 25.0
            bkg_status = "AVAILABLE"
            if stars == 0:
                if open_issues == 0:
                    bkg_score = 25.0
                    bkg_detail = "0 stars and 0 open issues/PRs (clean backlog)"
                elif open_issues <= 5:
                    bkg_score = 18.0
                    bkg_detail = f"0 stars and {open_issues} open issues/PRs (minimal backlog)"
                else:
                    bkg_score = 10.0
                    bkg_detail = f"0 stars and {open_issues} open issues/PRs (elevated backlog for new project)"
            else:
                ratio = (open_issues / stars) * 100.0
                if ratio <= 2.0:
                    bkg_score = 25.0
                    bkg_detail = f"{open_issues:,} open issues/PRs ({ratio:.1f}% of stars - responsive triage)"
                elif ratio <= 5.0:
                    bkg_score = 20.0
                    bkg_detail = f"{open_issues:,} open issues/PRs ({ratio:.1f}% of stars - manageable backlog)"
                elif ratio <= 10.0:
                    bkg_score = 15.0
                    bkg_detail = f"{open_issues:,} open issues/PRs ({ratio:.1f}% of stars - moderate backlog)"
                elif ratio <= 20.0:
                    bkg_score = 8.0
                    bkg_detail = f"{open_issues:,} open issues/PRs ({ratio:.1f}% of stars - heavy backlog)"
                else:
                    bkg_score = 3.0
                    bkg_detail = f"{open_issues:,} open issues/PRs ({ratio:.1f}% of stars - critical backlog debt)"
            earned_pts += bkg_score

            # 3. Community Adoption (Forks/Stars) (Max 20)
            com_max = 20.0
            avail_weight += 20.0
            com_status = "AVAILABLE"
            if stars == 0:
                if forks == 0:
                    com_score = 10.0
                    com_detail = "0 stars and 0 forks (early-stage baseline)"
                else:
                    com_score = 15.0
                    com_detail = f"{forks} forks with 0 stars (early community contributions)"
            else:
                f_ratio = (forks / stars) * 100.0
                if f_ratio >= 10.0:
                    com_score = 20.0
                    com_detail = f"{forks:,} forks ({f_ratio:.1f}% fork/star ratio - high community adoption)"
                elif f_ratio >= 5.0:
                    com_score = 16.0
                    com_detail = f"{forks:,} forks ({f_ratio:.1f}% fork/star ratio - healthy adoption)"
                elif f_ratio >= 2.0:
                    com_score = 12.0
                    com_detail = f"{forks:,} forks ({f_ratio:.1f}% fork/star ratio - moderate adoption)"
                else:
                    com_score = 8.0
                    com_detail = f"{forks:,} forks ({f_ratio:.1f}% fork/star ratio - low fork engagement)"
            earned_pts += com_score

            # 4. Project Hygiene (Max 10)
            hyg_max = 10.0
            avail_weight += 10.0
            hyg_status = "AVAILABLE"
            desc_pts = 5.0 if bool(desc) else 0.0
            lang_pts = 5.0 if lang and lang not in ("Unknown", "None", "") else 0.0
            hyg_score = desc_pts + lang_pts
            hyg_parts = []
            hyg_parts.append("description present" if desc_pts > 0 else "missing description")
            hyg_parts.append(f"language '{lang}'" if lang_pts > 0 else "unspecified language")
            hyg_detail = ", ".join(hyg_parts) + f" ({int(hyg_score)}/10 pts)"
            earned_pts += hyg_score

            # 5. Observed Snapshot Trends (Max 10)
            if snap_count < 2 or hist_status != "CALCULATED" or delta_stars is None or delta_issues is None:
                tr_score = None
                tr_max = 0.0
                tr_status = "INSUFFICIENT_DATA"
                tr_detail = f"Requires ≥ 2 collection cycles to observe trends (recorded: {snap_count})"
            else:
                tr_max = 10.0
                avail_weight += 10.0
                tr_status = "AVAILABLE"
                d_s = int(delta_stars)
                d_i = int(delta_issues)
                s_pts = 5.0 if d_s > 0 else (3.0 if d_s == 0 else 1.0)
                i_pts = 5.0 if d_i < 0 else (4.0 if d_i == 0 else 2.0)
                tr_score = s_pts + i_pts
                s_lbl = f"+{d_s} stars" if d_s > 0 else (f"{d_s} stars" if d_s < 0 else "stable stars")
                i_lbl = f"{d_i} issues/PRs (burn-down)" if d_i < 0 else (f"+{d_i} issues/PRs" if d_i > 0 else "stable issues")
                tr_detail = f"Observed over {snap_count} collections: {s_lbl}, {i_lbl} ({int(tr_score)}/10 pts)"
                earned_pts += tr_score

            # Final Normalization & Grade Calculation
            coverage_pct = round(avail_weight)
            if avail_weight >= 40.0:
                final_score = int(round((earned_pts / avail_weight) * 100.0))
                final_score = max(0, min(100, final_score))
                if final_score >= 90:
                    final_grade = "A"
                elif final_score >= 75:
                    final_grade = "B"
                elif final_score >= 60:
                    final_grade = "C"
                elif final_score >= 40:
                    final_grade = "D"
                else:
                    final_grade = "F"
                note_str = f"Grade {final_grade} ({final_score}/100) based on {coverage_pct}% data coverage."
            else:
                final_score = None
                final_grade = "N/A"
                note_str = f"Insufficient signals available ({coverage_pct}% coverage < 40% threshold)."

            records.append(
                {
                    "repo_id": r_id,
                    "full_name": full_name,
                    "health_score": final_score,
                    "health_grade": final_grade,
                    "data_coverage_pct": coverage_pct,
                    "evaluated_weight": int(avail_weight),
                    "points_earned": round(earned_pts, 1),
                    "recency_score": rec_score,
                    "recency_max": int(rec_max),
                    "recency_status": rec_status,
                    "recency_detail": rec_detail,
                    "backlog_score": bkg_score,
                    "backlog_max": int(bkg_max),
                    "backlog_status": bkg_status,
                    "backlog_detail": bkg_detail,
                    "community_score": com_score,
                    "community_max": int(com_max),
                    "community_status": com_status,
                    "community_detail": com_detail,
                    "hygiene_score": hyg_score,
                    "hygiene_max": int(hyg_max),
                    "hygiene_status": hyg_status,
                    "hygiene_detail": hyg_detail,
                    "trend_score": tr_score,
                    "trend_max": int(tr_max),
                    "trend_status": tr_status,
                    "trend_detail": tr_detail,
                    "notes": note_str,
                }
            )

        res_df = pd.DataFrame(records, columns=cols)
        return res_df.sort_values(
            by=["health_score", "repo_id"], ascending=[False, True], na_position="last"
        ).reset_index(drop=True)

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
        health_df = self.compute_repository_health_scores()

        export_paths: Dict[str, Path] = {
            "top_repositories": self.output_dir / "top_repositories.csv",
            "language_summary": self.output_dir / "language_summary.csv",
            "repository_activity": self.output_dir / "repository_activity.csv",
            "historical_changes": self.output_dir / "historical_changes.csv",
            "repository_health": self.output_dir / "repository_health.csv",
        }

        top_repos_df.to_csv(export_paths["top_repositories"], index=False, encoding="utf-8")
        lang_df.to_csv(export_paths["language_summary"], index=False, encoding="utf-8")
        activity_df.to_csv(export_paths["repository_activity"], index=False, encoding="utf-8")
        history_df.to_csv(export_paths["historical_changes"], index=False, encoding="utf-8")
        health_df.to_csv(export_paths["repository_health"], index=False, encoding="utf-8")

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
