"""GitPulse Ingestion Engine

Fetches public repository data from the GitHub REST API without authentication,
handles network conditions and rate limits, saves raw responses locally, extracts
structured metadata, and persists to local SQLite database with snapshot history.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure GitPulse project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests

try:
    from src.database import DatabaseManager, DEFAULT_DB_PATH
except ModuleNotFoundError:
    from database import DatabaseManager, DEFAULT_DB_PATH

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gitpulse.ingest")

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_USER_AGENT = "GitPulse-Analytics/1.0 (Local-First-Analytics-Pipeline)"


class GitPulseError(Exception):
    """Base exception for GitPulse errors."""


class GitHubNetworkError(GitPulseError):
    """Raised when network connectivity fails or requests time out."""


class GitHubRepoNotFoundError(GitPulseError):
    """Raised when the requested repository is not found (404)."""


class GitHubRateLimitError(GitPulseError):
    """Raised when GitHub API rate limits are exceeded (403/429)."""

    def __init__(self, message: str, reset_time: Optional[datetime] = None):
        super().__init__(message)
        self.reset_time = reset_time


class GitHubAPIError(GitPulseError):
    """Raised when GitHub API returns an unexpected error."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"GitHub API error {status_code}: {message}")
        self.status_code = status_code


class GitHubIngester:
    """Handles ingestion of repository metadata via the unauthenticated GitHub REST API."""

    def __init__(
        self,
        raw_data_dir: str | Path = "data/raw",
        timeout: int = 15,
        user_agent: str = DEFAULT_USER_AGENT,
        db_path: Optional[str | Path] = None,
        db_manager: Optional[DatabaseManager] = None,
    ):
        self.raw_data_dir = Path(raw_data_dir)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": user_agent,
            }
        )

        if db_manager is not None:
            self.db: Optional[DatabaseManager] = db_manager
        elif db_path is not None:
            self.db = DatabaseManager(db_path=db_path)
        else:
            self.db = None

    def check_rate_limit(self) -> Dict[str, Any]:
        """Check the current unauthenticated GitHub API rate limit status.

        Returns:
            Dictionary containing limit, remaining, and reset timestamp.
        """
        url = f"{GITHUB_API_BASE}/rate_limit"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            rate_data = resp.json().get("rate", {})
            reset_ts = rate_data.get("reset")
            reset_dt = (
                datetime.fromtimestamp(reset_ts, tz=timezone.utc)
                if reset_ts
                else None
            )
            return {
                "limit": rate_data.get("limit", 60),
                "remaining": rate_data.get("remaining", 0),
                "reset_timestamp": reset_ts,
                "reset_time_utc": reset_dt.isoformat() if reset_dt else None,
            }
        except requests.exceptions.RequestException as exc:
            logger.warning("Could not query rate limit endpoint: %s", exc)
            return {"limit": 60, "remaining": "unknown", "reset_time_utc": None}

    def fetch_repo_raw(self, owner: str, repo: str) -> Dict[str, Any]:
        """Fetch raw JSON repository payload from GitHub REST API.

        Args:
            owner: The repository owner (user or organization).
            repo: The repository name.

        Returns:
            Raw dictionary payload returned by GitHub.

        Raises:
            GitHubRepoNotFoundError: If repository is 404.
            GitHubRateLimitError: If rate limit is hit.
            GitHubNetworkError: If request fails or times out.
            GitHubAPIError: For other HTTP errors.
        """
        clean_owner = owner.strip()
        clean_repo = repo.strip()
        url = f"{GITHUB_API_BASE}/repos/{clean_owner}/{clean_repo}"

        logger.info("Fetching repository metadata for %s/%s...", clean_owner, clean_repo)

        try:
            resp = self.session.get(url, timeout=self.timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            msg = f"Network error connecting to GitHub API for {clean_owner}/{clean_repo}: {exc}"
            logger.error(msg)
            raise GitHubNetworkError(msg) from exc
        except requests.exceptions.RequestException as exc:
            msg = f"Request error for {clean_owner}/{clean_repo}: {exc}"
            logger.error(msg)
            raise GitHubNetworkError(msg) from exc

        # Handle rate limits
        if resp.status_code in (403, 429):
            remaining = resp.headers.get("X-RateLimit-Remaining")
            reset_epoch = resp.headers.get("X-RateLimit-Reset")
            reset_dt = None
            if reset_epoch and reset_epoch.isdigit():
                reset_dt = datetime.fromtimestamp(int(reset_epoch), tz=timezone.utc)

            if remaining == "0" or "rate limit" in resp.text.lower():
                reset_str = reset_dt.isoformat() if reset_dt else "unknown"
                msg = (
                    f"GitHub unauthenticated rate limit exceeded (60 req/hr). "
                    f"Limit resets at {reset_str} UTC."
                )
                logger.error(msg)
                raise GitHubRateLimitError(msg, reset_time=reset_dt)

        if resp.status_code == 404:
            msg = f"Repository {clean_owner}/{clean_repo} was not found (HTTP 404)."
            logger.error(msg)
            raise GitHubRepoNotFoundError(msg)

        if not resp.ok:
            try:
                error_body = resp.json().get("message", resp.text)
            except Exception:
                error_body = resp.text
            raise GitHubAPIError(resp.status_code, error_body)

        try:
            payload = resp.json()
        except ValueError as exc:
            msg = f"Failed to parse JSON response for {clean_owner}/{clean_repo}."
            logger.error(msg)
            raise GitHubAPIError(resp.status_code, msg) from exc

        return payload

    def save_raw_response(
        self, owner: str, repo: str, raw_payload: Dict[str, Any]
    ) -> Path:
        """Save raw JSON response to local storage under raw_data_dir.

        Args:
            owner: Repository owner.
            repo: Repository name.
            raw_payload: Raw JSON response dictionary.

        Returns:
            Path to the saved JSON file.
        """
        self.raw_data_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_owner = "".join(c for c in owner if c.isalnum() or c in ("-", "_"))
        safe_repo = "".join(c for c in repo if c.isalnum() or c in ("-", "_"))
        filename = f"{safe_owner}__{safe_repo}__{timestamp}.json"
        target_path = self.raw_data_dir / filename

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(raw_payload, f, indent=2, ensure_ascii=False)

        logger.info("Saved raw JSON payload to %s", target_path)
        return target_path

    @staticmethod
    def extract_metrics(raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract required Milestone 1 & 2 fields from raw repository JSON payload.

        Handles missing fields, nulls, and schema discrepancies safely.

        Args:
            raw_data: Raw JSON dictionary from GitHub API.

        Returns:
            Normalized dictionary containing:
            - repo_id: GitHub numeric ID
            - id: GitHub numeric ID (alias)
            - name: repository name
            - owner: owner login
            - description: repository description (or empty string)
            - primary_language: primary language (or 'None')
            - stars: stargazers count (integer)
            - forks: forks count (integer)
            - open_issues: open issues count (integer)
            - creation_date: ISO 8601 creation string
            - last_updated_date: ISO 8601 update string
            - url: HTML URL for the repository
        """
        owner_obj = raw_data.get("owner")
        owner_login = ""
        if isinstance(owner_obj, dict):
            owner_login = owner_obj.get("login") or ""
        elif isinstance(owner_obj, str):
            owner_login = owner_obj

        repo_id = int(raw_data.get("id") or 0)

        return {
            "repo_id": repo_id,
            "id": repo_id,
            "name": raw_data.get("name") or "",
            "owner": owner_login,
            "description": raw_data.get("description") or "",
            "primary_language": raw_data.get("language") or "None",
            "stars": int(raw_data.get("stargazers_count") or 0),
            "forks": int(raw_data.get("forks_count") or 0),
            "open_issues": int(raw_data.get("open_issues_count") or 0),
            "creation_date": raw_data.get("created_at") or "",
            "last_updated_date": raw_data.get("updated_at") or "",
            "url": raw_data.get("html_url") or "",
        }

    def ingest_repo(
        self,
        owner: str,
        repo: str,
        save_raw: bool = True,
    ) -> Dict[str, Any]:
        """Full ingestion flow for a single repository: fetch, save raw, and persist.

        Args:
            owner: Repository owner.
            repo: Repository name.
            save_raw: If True, writes raw payload to local data/raw directory.

        Returns:
            Dictionary with extracted metrics and metadata including raw_file_path.
        """
        raw_payload = self.fetch_repo_raw(owner, repo)
        raw_path = None
        if save_raw:
            raw_path = self.save_raw_response(owner, repo, raw_payload)

        metrics = self.extract_metrics(raw_payload)
        metrics["raw_file_path"] = str(raw_path) if raw_path else None

        # Persist to SQLite if database is configured
        if self.db and metrics.get("repo_id"):
            self.db.upsert_repository(metrics)
            snapshot_id = self.db.insert_snapshot(
                repo_id=metrics["repo_id"],
                stars=metrics["stars"],
                forks=metrics["forks"],
                open_issues=metrics["open_issues"],
            )
            metrics["snapshot_id"] = snapshot_id

        return metrics

    def ingest_multiple(
        self,
        repositories: List[str],
        save_raw: bool = True,
    ) -> List[Dict[str, Any]]:
        """Ingest a list of repositories specified in 'owner/repo' format.

        Tracks full pipeline run status and errors in the database.

        Args:
            repositories: List of repository strings like ['pallets/flask', 'psf/requests'].
            save_raw: If True, writes raw payload for each repository.

        Returns:
            List of extracted metric dictionaries for successfully ingested repositories.
        """
        run_id = None
        errors: List[str] = []
        if self.db:
            run_id = self.db.start_pipeline_run()

        results: List[Dict[str, Any]] = []
        for repo_identifier in repositories:
            parts = repo_identifier.strip().split("/")
            if len(parts) != 2:
                err_msg = f"Invalid repository identifier '{repo_identifier}'. Must be in 'owner/repo' format."
                logger.warning(err_msg)
                errors.append(err_msg)
                continue

            owner, repo_name = parts[0], parts[1]
            try:
                data = self.ingest_repo(owner, repo_name, save_raw=save_raw)
                results.append(data)
                logger.info("Successfully ingested %s (Stars: %d)", repo_identifier, data["stars"])
            except GitPulseError as exc:
                err_msg = f"{repo_identifier}: {exc}"
                errors.append(err_msg)
                logger.error("Failed to ingest %s: %s", repo_identifier, exc)

        if self.db and run_id is not None:
            if len(results) == len(repositories):
                status = "COMPLETED"
            elif len(results) > 0:
                status = "PARTIAL"
            else:
                status = "FAILED"

            self.db.complete_pipeline_run(
                run_id=run_id,
                status=status,
                successful_repositories=len(results),
                error_details=errors if errors else None,
            )

        return results


def main():
    """CLI entry point to run sample ingestion on real public repositories."""
    import argparse
    import pprint

    parser = argparse.ArgumentParser(
        description="GitPulse Ingestion Module (Milestone 2) - Local-First GitHub Ingest & SQLite"
    )
    parser.add_argument(
        "--repos",
        nargs="+",
        default=["psf/requests", "pallets/flask", "fastapi/fastapi"],
        help="One or more repositories in owner/repo format (default: psf/requests pallets/flask fastapi/fastapi)",
    )
    parser.add_argument(
        "--raw-dir",
        default="data/raw",
        help="Directory to save raw JSON responses (default: data/raw)",
    )
    parser.add_argument(
        "--db-path",
        default="data/gitpulse.db",
        help="Path to SQLite database file (default: data/gitpulse.db)",
    )
    parser.add_argument(
        "--check-rate-limit",
        action="store_true",
        help="Check and display current GitHub unauthenticated rate limit quota",
    )

    args = parser.parse_args()
    ingester = GitHubIngester(raw_data_dir=args.raw_dir, db_path=args.db_path)

    rate_status = ingester.check_rate_limit()
    print("=" * 60)
    print("GitPulse Ingestion Engine - Milestone 2")
    print(
        f"GitHub Rate Limit Remaining: {rate_status.get('remaining')}/{rate_status.get('limit')} "
        f"(Resets: {rate_status.get('reset_time_utc')})"
    )
    print("=" * 60)

    if args.check_rate_limit:
        return

    print(f"Target Repositories: {args.repos}")
    print(f"Database Target: {args.db_path}\n")
    results = ingester.ingest_multiple(args.repos, save_raw=True)

    print("\n" + "=" * 60)
    print(f"Ingestion Completed: {len(results)}/{len(args.repos)} repositories ingested successfully.")
    print("=" * 60)

    for item in results:
        print("\nExtracted Record:")
        pprint.pprint(item)


if __name__ == "__main__":
    main()
