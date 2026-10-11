"""Unit and integration tests for GitPulse Ingestion Engine (Milestone 1)."""

import json
from unittest.mock import MagicMock, patch
import pytest
import requests

from src.ingest import (
    GitHubAPIError,
    GitHubIngester,
    GitHubNetworkError,
    GitHubRateLimitError,
    GitHubRepoNotFoundError,
)


@pytest.fixture
def sample_raw_payload():
    """Sample realistic GitHub API response payload."""
    return {
        "id": 1234567,
        "name": "requests",
        "full_name": "psf/requests",
        "owner": {
            "login": "psf",
            "id": 501726,
            "type": "Organization",
        },
        "description": "A simple, yet elegant, HTTP library.",
        "language": "Python",
        "stargazers_count": 52000,
        "forks_count": 9200,
        "open_issues_count": 140,
        "created_at": "2011-02-13T18:38:17Z",
        "updated_at": "2026-10-10T12:00:00Z",
        "html_url": "https://github.com/psf/requests",
    }


def test_extract_metrics_complete_payload(sample_raw_payload):
    """Test extracting all 10 required fields from a complete payload."""
    metrics = GitHubIngester.extract_metrics(sample_raw_payload)

    assert metrics["name"] == "requests"
    assert metrics["owner"] == "psf"
    assert metrics["description"] == "A simple, yet elegant, HTTP library."
    assert metrics["primary_language"] == "Python"
    assert metrics["stars"] == 52000
    assert metrics["forks"] == 9200
    assert metrics["open_issues"] == 140
    assert metrics["creation_date"] == "2011-02-13T18:38:17Z"
    assert metrics["last_updated_date"] == "2026-10-10T12:00:00Z"
    assert metrics["url"] == "https://github.com/psf/requests"


def test_extract_metrics_missing_or_null_fields():
    """Test resilience against missing, null, or irregular schema fields."""
    sparse_payload = {
        "name": "minimal-repo",
        "owner": None,
        "description": None,
        "language": None,
        "stargazers_count": None,
        "forks_count": None,
        "open_issues_count": None,
        "created_at": None,
        "updated_at": None,
        "html_url": None,
    }
    metrics = GitHubIngester.extract_metrics(sparse_payload)

    assert metrics["name"] == "minimal-repo"
    assert metrics["owner"] == ""
    assert metrics["description"] == ""
    assert metrics["primary_language"] == "None"
    assert metrics["stars"] == 0
    assert metrics["forks"] == 0
    assert metrics["open_issues"] == 0
    assert metrics["creation_date"] == ""
    assert metrics["last_updated_date"] == ""
    assert metrics["url"] == ""


def test_save_raw_response(tmp_path, sample_raw_payload):
    """Test that raw JSON is saved to disk and matches original structure."""
    ingester = GitHubIngester(raw_data_dir=tmp_path)
    file_path = ingester.save_raw_response("psf", "requests", sample_raw_payload)

    assert file_path.exists()
    assert file_path.suffix == ".json"
    with open(file_path, "r", encoding="utf-8") as f:
        loaded_data = json.load(f)

    assert loaded_data["id"] == sample_raw_payload["id"]
    assert loaded_data["name"] == "requests"


@patch("requests.Session.get")
def test_rate_limit_exceeded(mock_get):
    """Test that 403 with X-RateLimit-Remaining=0 raises GitHubRateLimitError."""
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.headers = {
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": "1770000000",
    }
    mock_response.text = "API rate limit exceeded"
    mock_get.return_value = mock_response

    ingester = GitHubIngester()
    with pytest.raises(GitHubRateLimitError) as exc_info:
        ingester.fetch_repo_raw("owner", "repo")

    assert "rate limit exceeded" in str(exc_info.value).lower()
    assert exc_info.value.reset_time is not None


@patch("requests.Session.get")
def test_repository_not_found(mock_get):
    """Test that 404 raises GitHubRepoNotFoundError."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.headers = {}
    mock_response.text = '{"message": "Not Found"}'
    mock_get.return_value = mock_response

    ingester = GitHubIngester()
    with pytest.raises(GitHubRepoNotFoundError):
        ingester.fetch_repo_raw("nonexistent-owner", "nonexistent-repo")


@patch("requests.Session.get")
def test_network_timeout(mock_get):
    """Test that connection timeouts raise GitHubNetworkError."""
    mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")

    ingester = GitHubIngester()
    with pytest.raises(GitHubNetworkError):
        ingester.fetch_repo_raw("psf", "requests")


def test_rate_limit_checker():
    """Test check_rate_limit query returns valid status format."""
    ingester = GitHubIngester()
    rate = ingester.check_rate_limit()
    assert "limit" in rate
    assert "remaining" in rate
