"""GitPulse Database Management Module (Milestone 2).

Manages local SQLite storage for repositories, historical metric snapshots,
and pipeline execution runs with primary keys, foreign keys, and UPSERT logic.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
import sqlite3

logger = logging.getLogger("gitpulse.database")

DEFAULT_DB_PATH = Path("data/gitpulse.db")


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class DatabaseManager:
    """Manages SQLite database connection, schema migration, and ELT persistence."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.init_db()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding a SQLite connection with foreign keys enabled."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Enable foreign key enforcement in SQLite
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Initialize database schema with tables and indexes if they do not exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Repositories table (Stable GitHub repo_id as Primary Key)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS repositories (
                    repo_id INTEGER PRIMARY KEY,
                    owner TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    primary_language TEXT,
                    stars INTEGER NOT NULL DEFAULT 0,
                    forks INTEGER NOT NULL DEFAULT 0,
                    open_issues INTEGER NOT NULL DEFAULT 0,
                    creation_date TEXT,
                    last_updated_date TEXT,
                    url TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                );
                """
            )

            # 2. Repository Snapshots table (Historical metrics over time)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS repository_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_id INTEGER NOT NULL,
                    collection_timestamp TEXT NOT NULL,
                    stars INTEGER NOT NULL,
                    forks INTEGER NOT NULL,
                    open_issues INTEGER NOT NULL,
                    FOREIGN KEY (repo_id) REFERENCES repositories(repo_id) ON DELETE CASCADE
                );
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_repo_time
                ON repository_snapshots (repo_id, collection_timestamp);
                """
            )

            # 3. Pipeline Runs table (Execution tracking & error auditing)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    status TEXT NOT NULL,
                    successful_repositories INTEGER NOT NULL DEFAULT 0,
                    error_details TEXT
                );
                """
            )
            logger.info("Database initialized successfully at %s", self.db_path)

    def upsert_repository(self, metrics: Dict[str, Any]) -> None:
        """Insert or update a repository record using SQLite UPSERT logic.

        If the repository already exists (by repo_id), update its fields to
        the latest collected values instead of inserting duplicate rows.

        Args:
            metrics: Dictionary containing repository metadata.
        """
        repo_id = metrics.get("repo_id") or metrics.get("id")
        if not repo_id:
            raise ValueError("Repository metrics must contain a valid non-zero 'repo_id'.")

        ingested_at = metrics.get("ingested_at") or utc_now_iso()

        sql = """
            INSERT INTO repositories (
                repo_id, owner, name, description, primary_language,
                stars, forks, open_issues, creation_date, last_updated_date,
                url, ingested_at
            ) VALUES (
                :repo_id, :owner, :name, :description, :primary_language,
                :stars, :forks, :open_issues, :creation_date, :last_updated_date,
                :url, :ingested_at
            )
            ON CONFLICT(repo_id) DO UPDATE SET
                owner = excluded.owner,
                name = excluded.name,
                description = excluded.description,
                primary_language = excluded.primary_language,
                stars = excluded.stars,
                forks = excluded.forks,
                open_issues = excluded.open_issues,
                creation_date = excluded.creation_date,
                last_updated_date = excluded.last_updated_date,
                url = excluded.url,
                ingested_at = excluded.ingested_at;
        """

        params = {
            "repo_id": int(repo_id),
            "owner": str(metrics.get("owner") or ""),
            "name": str(metrics.get("name") or ""),
            "description": str(metrics.get("description") or ""),
            "primary_language": str(metrics.get("primary_language") or "None"),
            "stars": int(metrics.get("stars") or 0),
            "forks": int(metrics.get("forks") or 0),
            "open_issues": int(metrics.get("open_issues") or 0),
            "creation_date": str(metrics.get("creation_date") or ""),
            "last_updated_date": str(metrics.get("last_updated_date") or ""),
            "url": str(metrics.get("url") or ""),
            "ingested_at": ingested_at,
        }

        with self.get_connection() as conn:
            conn.execute(sql, params)
            logger.debug("Upserted repository %s (ID: %s)", params["name"], repo_id)

    def insert_snapshot(
        self,
        repo_id: int,
        stars: int,
        forks: int,
        open_issues: int,
        collection_timestamp: Optional[str] = None,
    ) -> int:
        """Insert a new historical metric snapshot for a repository.

        Args:
            repo_id: The foreign key repository ID.
            stars: Number of stars at this snapshot.
            forks: Number of forks at this snapshot.
            open_issues: Number of open issues at this snapshot.
            collection_timestamp: Optional ISO timestamp (defaults to current UTC time).

        Returns:
            The generated snapshot ID.
        """
        ts = collection_timestamp or utc_now_iso()
        sql = """
            INSERT INTO repository_snapshots (
                repo_id, collection_timestamp, stars, forks, open_issues
            ) VALUES (?, ?, ?, ?, ?);
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                sql,
                (
                    int(repo_id),
                    ts,
                    int(stars or 0),
                    int(forks or 0),
                    int(open_issues or 0),
                ),
            )
            snapshot_id = cursor.lastrowid
            logger.debug("Saved snapshot %d for repository ID %d", snapshot_id, repo_id)
            return int(snapshot_id)

    def start_pipeline_run(self) -> int:
        """Record the start of a pipeline run.

        Returns:
            The newly created run_id.
        """
        sql = """
            INSERT INTO pipeline_runs (
                start_time, status, successful_repositories, error_details
            ) VALUES (?, ?, ?, ?);
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (utc_now_iso(), "RUNNING", 0, None))
            run_id = cursor.lastrowid
            logger.info("Started pipeline run ID: %d", run_id)
            return int(run_id)

    def complete_pipeline_run(
        self,
        run_id: int,
        status: str,
        successful_repositories: int,
        error_details: Optional[str | List[str] | Dict[str, Any]] = None,
    ) -> None:
        """Record the completion (or failure) of a pipeline run.

        Args:
            run_id: ID of the pipeline run.
            status: Status ('COMPLETED', 'PARTIAL', 'FAILED').
            successful_repositories: Number of successfully ingested repositories.
            error_details: String or serializable error details.
        """
        err_str: Optional[str] = None
        if error_details:
            if isinstance(error_details, (dict, list)):
                err_str = json.dumps(error_details)
            else:
                err_str = str(error_details)

        sql = """
            UPDATE pipeline_runs
            SET end_time = ?,
                status = ?,
                successful_repositories = ?,
                error_details = ?
            WHERE run_id = ?;
        """
        with self.get_connection() as conn:
            conn.execute(sql, (utc_now_iso(), status, int(successful_repositories), err_str, int(run_id)))
            logger.info(
                "Completed pipeline run ID: %d [Status: %s, Success: %d]",
                run_id,
                status,
                successful_repositories,
            )

    def get_repository(self, repo_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single repository record by repo_id."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM repositories WHERE repo_id = ?", (int(repo_id),)
            ).fetchone()
            return dict(row) if row else None

    def get_all_repositories(self) -> List[Dict[str, Any]]:
        """Fetch all repositories ordered by stars descending."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM repositories ORDER BY stars DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_snapshots(self, repo_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch historical snapshots, optionally filtered by repo_id."""
        with self.get_connection() as conn:
            if repo_id is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM repository_snapshots
                    WHERE repo_id = ?
                    ORDER BY collection_timestamp ASC
                    """,
                    (int(repo_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM repository_snapshots
                    ORDER BY collection_timestamp ASC
                    """
                ).fetchall()
            return [dict(r) for r in rows]

    def get_pipeline_runs(self) -> List[Dict[str, Any]]:
        """Fetch all pipeline runs ordered by run_id descending."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM pipeline_runs ORDER BY run_id DESC"
            ).fetchall()
            return [dict(r) for r in rows]


def main():
    """CLI utility to inspect the local database."""
    import argparse
    import pprint

    parser = argparse.ArgumentParser(description="GitPulse Database Inspection (Milestone 2)")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Path to SQLite database")
    parser.add_argument("--summary", action="store_true", help="Print summary of tables")
    parser.add_argument("--repos", action="store_true", help="Print all repository records")
    parser.add_argument("--snapshots", action="store_true", help="Print all historical snapshots")
    parser.add_argument("--runs", action="store_true", help="Print all pipeline execution runs")

    args = parser.parse_args()
    db = DatabaseManager(db_path=args.db_path)

    repos = db.get_all_repositories()
    snapshots = db.get_snapshots()
    runs = db.get_pipeline_runs()

    print("=" * 60)
    print(f"GitPulse SQLite Database: {db.db_path}")
    print(f"Repositories: {len(repos)} | Snapshots: {len(snapshots)} | Pipeline Runs: {len(runs)}")
    print("=" * 60)

    if args.repos or (not args.snapshots and not args.runs):
        print("\n--- Repositories ---")
        for r in repos:
            print(
                f"[ID: {r['repo_id']}] {r['owner']}/{r['name']} | "
                f"Stars: {r['stars']:,} | Forks: {r['forks']:,} | Issues: {r['open_issues']} | Lang: {r['primary_language']}"
            )

    if args.snapshots:
        print("\n--- Historical Snapshots ---")
        for s in snapshots:
            print(
                f"[Snapshot #{s['id']}] Repo ID: {s['repo_id']} at {s['collection_timestamp']} -> "
                f"Stars: {s['stars']:,}, Forks: {s['forks']:,}, Issues: {s['open_issues']}"
            )

    if args.runs:
        print("\n--- Pipeline Runs ---")
        for run in runs:
            print(
                f"[Run #{run['run_id']}] {run['start_time']} -> {run['end_time']} | "
                f"Status: {run['status']} | Success: {run['successful_repositories']} | Errors: {run['error_details']}"
            )


if __name__ == "__main__":
    main()
