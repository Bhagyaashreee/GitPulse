"""GitPulse Interactive Analytics Dashboard (Milestone 4).

Streamlit & Plotly dark-themed dashboard presenting local-first GitHub analytics:
- High-level KPIs & Overview
- Repository Rankings & Direct Comparisons
- Programming Language Distribution & Aggregates
- Repository Maintenance Activity & Health Ratios
- Time-Series Snapshot Trend Analysis
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Ensure GitPulse project root is on sys.path when launched directly via 'streamlit run src/dashboard.py'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from src.database import DEFAULT_DB_PATH, DatabaseManager
    from src.transform import DEFAULT_PROCESSED_DIR, GitPulseTransformer
    from src.ingest import (
        DEFAULT_USER_AGENT,
        GitHubAPIError,
        GitHubIngester,
        GitHubNetworkError,
        GitHubRateLimitError,
        GitHubRepoNotFoundError,
        GitPulseError,
    )
except ModuleNotFoundError:
    from database import DEFAULT_DB_PATH, DatabaseManager
    from transform import DEFAULT_PROCESSED_DIR, GitPulseTransformer
    from ingest import (
        DEFAULT_USER_AGENT,
        GitHubAPIError,
        GitHubIngester,
        GitHubNetworkError,
        GitHubRateLimitError,
        GitHubRepoNotFoundError,
        GitPulseError,
    )

# -----------------------------------------------------------------------------
# Configuration & Theme Settings
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="GitPulse | GitHub Analytics",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Dark Navy Data-Product CSS
CUSTOM_CSS = """
<style>
    /* Global Base */
    .stApp {
        background-color: #0A0F1D;
        color: #E2E8F0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    /* Top Header */
    .dashboard-header {
        padding: 0.5rem 0rem 1.5rem 0rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        margin-bottom: 1.5rem;
    }
    .dashboard-title {
        font-size: 2.1rem;
        font-weight: 800;
        letter-spacing: -0.03em;
        background: linear-gradient(135deg, #38BDF8 0%, #818CF8 50%, #C084FC 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0;
    }
    .dashboard-subtitle {
        color: #94A3B8;
        font-size: 0.95rem;
        margin-top: 0.3rem;
    }

    /* KPI Metric Cards */
    .kpi-container {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 1rem;
        margin-bottom: 1.5rem;
    }
    .kpi-card {
        background: #131E36;
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 12px;
        padding: 1.1rem 1.2rem;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
        transition: transform 0.15s ease, border-color 0.15s ease;
    }
    .kpi-card:hover {
        border-color: rgba(56, 189, 248, 0.3);
        transform: translateY(-2px);
    }
    .kpi-label {
        font-size: 0.78rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #94A3B8;
        margin-bottom: 0.3rem;
    }
    .kpi-value {
        font-size: 1.85rem;
        font-weight: 700;
        color: #F8FAFC;
        line-height: 1.2;
    }
    .kpi-sub {
        font-size: 0.8rem;
        color: #38BDF8;
        margin-top: 0.3rem;
    }

    /* Section Cards */
    .section-card {
        background: #111A30;
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 12px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 1.5rem;
    }
    .section-title {
        font-size: 1.15rem;
        font-weight: 700;
        color: #F1F5F9;
        margin-bottom: 0.8rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    /* Badges */
    .badge-pill {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        background: rgba(56, 189, 248, 0.15);
        color: #38BDF8;
        border: 1px solid rgba(56, 189, 248, 0.3);
    }
    .badge-pill-success {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        background: rgba(52, 211, 153, 0.15);
        color: #34D399;
        border: 1px solid rgba(52, 211, 153, 0.3);
    }
    .badge-pill-danger {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        background: rgba(244, 63, 94, 0.15);
        color: #F43F5E;
        border: 1px solid rgba(244, 63, 94, 0.3);
    }
    .badge-pill-warning {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        background: rgba(251, 191, 36, 0.15);
        color: #FBBF24;
        border: 1px solid rgba(251, 191, 36, 0.3);
    }

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: #0D1527;
        border-right: 1px solid rgba(255, 255, 255, 0.08);
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

PLOTLY_LAYOUT_DEFAULTS = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#E2E8F0", family="-apple-system, sans-serif"),
    margin=dict(l=20, r=20, t=40, b=20),
    xaxis=dict(
        gridcolor="rgba(255, 255, 255, 0.08)",
        zerolinecolor="rgba(255, 255, 255, 0.15)",
        tickfont=dict(color="#94A3B8"),
    ),
    yaxis=dict(
        gridcolor="rgba(255, 255, 255, 0.08)",
        zerolinecolor="rgba(255, 255, 255, 0.15)",
        tickfont=dict(color="#94A3B8"),
    ),
    legend=dict(
        font=dict(color="#E2E8F0"),
        bgcolor="rgba(19, 30, 54, 0.6)",
        bordercolor="rgba(255,255,255,0.08)",
    ),
)


# -----------------------------------------------------------------------------
# Data Ingestion & Transformation Bridge
# -----------------------------------------------------------------------------
def ensure_database_initialized(db_path_str: str = str(DEFAULT_DB_PATH)) -> DatabaseManager:
    """Ensure the SQLite database file and required schema tables exist safely."""
    db_path = Path(db_path_str)
    return DatabaseManager(db_path=db_path)


@st.cache_data(show_spinner=False)
def load_analytics_data(
    db_path_str: str = str(DEFAULT_DB_PATH),
) -> Dict[str, pd.DataFrame]:
    """Load transformed analytics datasets from the SQLite database."""
    db_manager = ensure_database_initialized(db_path_str)
    transformer = GitPulseTransformer(db_manager=db_manager)

    top_repos = transformer.compute_top_repositories()
    languages = transformer.compute_language_summary()
    activity = transformer.compute_repository_activity()
    history = transformer.compute_historical_changes()
    raw_repos = transformer.load_raw_repositories()
    raw_snaps = transformer.load_raw_snapshots()
    runs_list = db_manager.get_pipeline_runs()
    pipeline_runs = pd.DataFrame(runs_list) if runs_list else pd.DataFrame()

    return {
        "top_repos": top_repos,
        "languages": languages,
        "activity": activity,
        "history": history,
        "raw_repos": raw_repos,
        "raw_snaps": raw_snaps,
        "pipeline_runs": pipeline_runs,
    }


def refresh_pipeline(db_path_str: str = str(DEFAULT_DB_PATH)) -> None:
    """Trigger full transformation recalculation and export CSVs."""
    db_path = Path(db_path_str)
    if not db_path.exists():
        ensure_database_initialized(db_path_str)
    transformer = GitPulseTransformer(db_path=db_path)
    transformer.export_all()
    st.cache_data.clear()


def trigger_ingestion(
    repos: list[str],
    db_path_str: str = str(DEFAULT_DB_PATH),
) -> tuple[bool, str]:
    """Trigger intentional ingestion of public repositories into SQLite database.

    Returns:
        Tuple of (success_boolean, status_message)
    """
    db_manager = ensure_database_initialized(db_path_str)
    ingester = GitHubIngester(db_manager=db_manager)

    try:
        # Check rate limit before issuing repository requests
        rate = ingester.check_rate_limit()
        remaining = rate.get("remaining")
        if remaining == 0:
            reset_utc = rate.get("reset_time_utc", "the next reset window")
            return (
                False,
                f"GitHub unauthenticated rate limit reached (0 remaining). Resets at {reset_utc} UTC.",
            )

        results = ingester.ingest_multiple(repos, save_raw=True)
        if not results:
            return (
                False,
                "No repositories were ingested. Please verify the repository names (format: owner/repo).",
            )

        # Recalculate analytics and export CSVs
        refresh_pipeline(db_path_str)
        return True, f"Successfully ingested {len(results)} of {len(repos)} repositories!"

    except GitHubRateLimitError as exc:
        return False, f"Rate limit error: {exc}"
    except (GitHubNetworkError, GitPulseError) as exc:
        return False, f"Ingestion error: {exc}"
    except Exception as exc:
        return False, f"Unexpected error during ingestion: {exc}"


# -----------------------------------------------------------------------------
# Dashboard Layout & Sections
# -----------------------------------------------------------------------------
def render_header(pipeline_runs: pd.DataFrame) -> None:
    """Render top header with system status pill and last successful run info."""
    col1, col2 = st.columns([2.5, 1.5])
    with col1:
        st.markdown(
            """
            <div class="dashboard-header">
                <h1 class="dashboard-title">⚡ GitPulse Analytics</h1>
                <div class="dashboard-subtitle">Local-First GitHub ELT Intelligence & Engineering Metrics</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col2:
        if not pipeline_runs.empty:
            latest = pipeline_runs.iloc[0]
            latest_status = str(latest.get("status", "UNKNOWN")).upper()
            latest_id = latest.get("run_id", "?")
            runs_count = len(pipeline_runs)

            badge_class = "badge-pill"
            if latest_status == "COMPLETED":
                badge_class = "badge-pill-success"
            elif latest_status == "FAILED":
                badge_class = "badge-pill-danger"
            elif latest_status == "PARTIAL":
                badge_class = "badge-pill-warning"

            # Check if there is a separate latest successful run
            successful_runs = pipeline_runs[pipeline_runs["status"] == "COMPLETED"]
            last_successful_html = ""
            if latest_status != "COMPLETED" and not successful_runs.empty:
                last_succ = successful_runs.iloc[0]
                succ_id = last_succ.get("run_id")
                succ_repos = last_succ.get("successful_repositories", 0)
                last_successful_html = (
                    f'<div style="font-size: 0.75rem; color: #34D399; margin-top: 0.25rem;">'
                    f'✓ Last Successful: Run #{succ_id} ({succ_repos} repos)'
                    f'</div>'
                )

            st.markdown(
                f"""
                <div style="text-align: right; padding-top: 0.8rem;">
                    <div>
                        <span class="{badge_class}">Latest Run #{latest_id}: {latest_status}</span>
                    </div>
                    {last_successful_html}
                    <div style="font-size: 0.75rem; color: #94A3B8; margin-top: 0.25rem;">
                        Total Runs: {runs_count} | Local SQLite Storage
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_overview(data: Dict[str, pd.DataFrame]) -> None:
    """Render KPI metrics summary cards."""
    top_repos = data["top_repos"]
    languages = data["languages"]

    if top_repos.empty:
        st.info("No repository data available to display overview.")
        return

    total_repos = len(top_repos)
    total_stars = int(top_repos["stars"].sum())
    total_forks = int(top_repos["forks"].sum())
    total_issues = int(top_repos["open_issues"].sum())

    most_starred = top_repos.iloc[0]["full_name"] if total_repos > 0 else "N/A"
    most_starred_count = top_repos.iloc[0]["stars"] if total_repos > 0 else 0

    top_lang = "Unknown"
    if not languages.empty:
        top_lang = languages.iloc[0]["primary_language"]

    st.markdown(
        f"""
        <div class="kpi-container">
            <div class="kpi-card">
                <div class="kpi-label">Repositories Tracked</div>
                <div class="kpi-value">{total_repos}</div>
                <div class="kpi-sub">Local SQLite Storage</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Total Stars</div>
                <div class="kpi-value">{total_stars:,}</div>
                <div class="kpi-sub">Across All Repos</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Total Forks</div>
                <div class="kpi-value">{total_forks:,}</div>
                <div class="kpi-sub">Ecosystem Forks</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Open Issues</div>
                <div class="kpi-value">{total_issues:,}</div>
                <div class="kpi-sub">Active Backlog</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Top Repository</div>
                <div class="kpi-value" style="font-size: 1.35rem; word-break: break-all;">{most_starred}</div>
                <div class="kpi-sub">★ {most_starred_count:,} stars</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Primary Language</div>
                <div class="kpi-value">{top_lang}</div>
                <div class="kpi-sub">Dominant Language</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_repository_rankings(data: Dict[str, pd.DataFrame]) -> None:
    """Render interactive repository tables, filters, and star vs fork comparisons."""
    top_repos = data["top_repos"]
    if top_repos.empty:
        return

    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">📊 Repository Rankings & Comparisons</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Filtering Controls
    f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
    with f_col1:
        search_query = st.text_input(
            "Search Repository Name",
            placeholder="e.g. flask, requests, fastapi",
            key="repo_search",
        )
    with f_col2:
        all_langs = sorted(list(top_repos["primary_language"].unique()))
        selected_langs = st.multiselect(
            "Filter Language",
            options=all_langs,
            default=all_langs,
            key="repo_lang_filter",
        )
    with f_col3:
        min_stars = st.number_input(
            "Min Stars",
            min_value=0,
            value=0,
            step=5000,
            key="repo_min_stars",
        )

    # Apply filters
    filtered_df = top_repos.copy()
    if search_query:
        filtered_df = filtered_df[
            filtered_df["full_name"].str.contains(search_query, case=False, na=False)
        ]
    if selected_langs:
        filtered_df = filtered_df[filtered_df["primary_language"].isin(selected_langs)]
    if min_stars > 0:
        filtered_df = filtered_df[filtered_df["stars"] >= min_stars]

    # Side-by-side Table and Chart
    chart_col, table_col = st.columns([1, 1])

    with chart_col:
        st.markdown("**Stars vs. Forks Comparison**")
        if not filtered_df.empty:
            chart_data = filtered_df.sort_values(by="stars", ascending=True)
            fig = go.Figure()
            fig.add_trace(
                go.Bar(
                    y=chart_data["full_name"],
                    x=chart_data["stars"],
                    name="Stars",
                    orientation="h",
                    marker=dict(color="#38BDF8"),
                    hovertemplate="%{y}<br>Stars: %{x:,}<extra></extra>",
                )
            )
            fig.add_trace(
                go.Bar(
                    y=chart_data["full_name"],
                    x=chart_data["forks"],
                    name="Forks",
                    orientation="h",
                    marker=dict(color="#818CF8"),
                    hovertemplate="%{y}<br>Forks: %{x:,}<extra></extra>",
                )
            )
            fig.update_layout(
                **PLOTLY_LAYOUT_DEFAULTS,
                barmode="group",
                height=340,
                xaxis_title="Count",
                yaxis_title="",
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No repositories match the current filters.")

    with table_col:
        st.markdown("**Ranked Repository List**")
        display_cols = [
            "star_rank",
            "fork_rank",
            "full_name",
            "primary_language",
            "stars",
            "forks",
            "open_issues",
        ]
        st.dataframe(
            filtered_df[display_cols].rename(
                columns={
                    "star_rank": "Star #",
                    "fork_rank": "Fork #",
                    "full_name": "Repository",
                    "primary_language": "Language",
                    "stars": "Stars",
                    "forks": "Forks",
                    "open_issues": "Issues",
                }
            ),
            width="stretch",
            hide_index=True,
            height=340,
        )


def render_language_analytics(data: Dict[str, pd.DataFrame]) -> None:
    """Render language breakdown by repository count, total stars, and average stars."""
    languages = data["languages"]
    if languages.empty:
        return

    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">🌐 Programming Language Analytics</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)

    # 1. Repo Count Distribution
    with c1:
        st.markdown("**Repository Share by Language**")
        fig_pie = px.pie(
            languages,
            names="primary_language",
            values="repo_count",
            color_discrete_sequence=["#38BDF8", "#818CF8", "#34D399", "#F43F5E", "#FBBF24"],
            hole=0.45,
        )
        fig_pie.update_layout(**PLOTLY_LAYOUT_DEFAULTS, height=280)
        st.plotly_chart(fig_pie, width="stretch")

    # 2. Total Stars by Language
    with c2:
        st.markdown("**Total Stars by Language**")
        fig_tot = px.bar(
            languages,
            x="primary_language",
            y="total_stars",
            text_auto=".2s",
            color="primary_language",
            color_discrete_sequence=["#38BDF8", "#818CF8", "#34D399"],
        )
        fig_tot.update_layout(
            **PLOTLY_LAYOUT_DEFAULTS,
            height=280,
            showlegend=False,
            xaxis_title="Language",
            yaxis_title="Total Stars",
        )
        st.plotly_chart(fig_tot, width="stretch")

    # 3. Average Stars per Repository
    with c3:
        st.markdown("**Average Stars per Repository**")
        fig_avg = px.bar(
            languages,
            x="primary_language",
            y="avg_stars",
            text_auto=".2s",
            color="primary_language",
            color_discrete_sequence=["#818CF8", "#38BDF8", "#34D399"],
        )
        fig_avg.update_layout(
            **PLOTLY_LAYOUT_DEFAULTS,
            height=280,
            showlegend=False,
            xaxis_title="Language",
            yaxis_title="Avg Stars",
        )
        st.plotly_chart(fig_avg, width="stretch")


def render_repository_activity(data: Dict[str, pd.DataFrame]) -> None:
    """Render repository age, update latency, and issue health ratios."""
    activity = data["activity"]
    if activity.empty:
        return

    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">⏱️ Repository Activity & Health Metrics</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown("**Open Issues vs. Star Density (%)**")
        fig_scatter = px.scatter(
            activity,
            x="stars",
            y="open_issues",
            size="forks",
            color="primary_language",
            text="full_name",
            hover_data=["age_days", "days_since_update", "issue_to_star_pct"],
            color_discrete_sequence=["#38BDF8", "#818CF8", "#34D399"],
        )
        fig_scatter.update_traces(textposition="top center")
        fig_scatter.update_layout(
            **PLOTLY_LAYOUT_DEFAULTS,
            height=320,
            xaxis_title="Total Stars",
            yaxis_title="Open Issues",
        )
        st.plotly_chart(fig_scatter, width="stretch")

    with col2:
        st.markdown("**Repository Age vs. Days Since Last GitHub Update**")
        # Horizontal comparison of update latency
        act_sorted = activity.sort_values(by="days_since_update", ascending=True)
        fig_lat = px.bar(
            act_sorted,
            y="full_name",
            x="days_since_update",
            orientation="h",
            color_discrete_sequence=["#34D399"],
            labels={"days_since_update": "Days Since Update", "full_name": ""},
        )
        fig_lat.update_layout(
            **PLOTLY_LAYOUT_DEFAULTS,
            height=320,
            xaxis_title="Days Since Last Update",
        )
        st.plotly_chart(fig_lat, width="stretch")


def render_historical_trends(data: Dict[str, pd.DataFrame]) -> None:
    """Render historical trend lines using repository_snapshots."""
    raw_snaps = data["raw_snaps"]
    top_repos = data["top_repos"]
    history = data["history"]

    st.markdown(
        """
        <div class="section-card">
            <div class="section-title">📈 Historical Snapshot Trends</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if top_repos.empty or raw_snaps.empty:
        st.info("No historical snapshots have been recorded in the database yet.")
        return

    # Repository selector
    repo_options = top_repos["full_name"].tolist()
    repo_map = dict(zip(top_repos["full_name"], top_repos["repo_id"]))

    selected_repo_name = st.selectbox(
        "Select Repository to Inspect Snapshot Timeline",
        options=repo_options,
        index=0,
        key="trend_repo_select",
    )

    selected_repo_id = repo_map[selected_repo_name]
    repo_snapshots = raw_snaps[raw_snaps["repo_id"] == selected_repo_id].sort_values(
        by="collection_timestamp", ascending=True
    )

    snapshot_count = len(repo_snapshots)

    # Check historical status from transformed table
    hist_row = history[history["repo_id"] == selected_repo_id]
    notes = hist_row.iloc[0]["notes"] if not hist_row.empty else ""

    if snapshot_count == 0:
        st.warning(f"No historical snapshots found for {selected_repo_name}.")
        return
    elif snapshot_count == 1:
        st.info(
            f"ℹ️ **Only 1 snapshot recorded for {selected_repo_name}** at "
            f"`{repo_snapshots.iloc[0]['collection_timestamp']}`.\n\n"
            f"*Historical velocity and trend charts require at least 2 snapshot collection cycles.* "
            f"Run ingestion again (`python -m src.ingest`) to record incremental snapshots over time."
        )
        # Display single snapshot snapshot point
        snap = repo_snapshots.iloc[0]
        st.markdown(
            f"""
            - **Current Stars:** {snap['stars']:,}
            - **Current Forks:** {snap['forks']:,}
            - **Open Issues:** {snap['open_issues']:,}
            """
        )
        return

    # Check if delta is zero across collections
    first_snap = repo_snapshots.iloc[0]
    last_snap = repo_snapshots.iloc[-1]
    is_delta_zero = (
        first_snap["stars"] == last_snap["stars"]
        and first_snap["forks"] == last_snap["forks"]
        and first_snap["open_issues"] == last_snap["open_issues"]
    )

    if is_delta_zero:
        st.caption(
            f"📌 {notes} Metrics are consistent across these snapshots (no change detected between collections)."
        )

    # Plot metric trend across collection timestamps
    fig_trend = go.Figure()

    fig_trend.add_trace(
        go.Scatter(
            x=repo_snapshots["collection_timestamp"],
            y=repo_snapshots["stars"],
            mode="lines+markers",
            name="Stars",
            line=dict(color="#38BDF8", width=3),
            marker=dict(size=8),
            hovertemplate="%{x}<br>Stars: %{y:,}<extra></extra>",
        )
    )

    fig_trend.add_trace(
        go.Scatter(
            x=repo_snapshots["collection_timestamp"],
            y=repo_snapshots["forks"],
            mode="lines+markers",
            name="Forks",
            line=dict(color="#818CF8", width=2),
            marker=dict(size=6),
            hovertemplate="%{x}<br>Forks: %{y:,}<extra></extra>",
        )
    )

    fig_trend.add_trace(
        go.Scatter(
            x=repo_snapshots["collection_timestamp"],
            y=repo_snapshots["open_issues"],
            mode="lines+markers",
            name="Open Issues",
            line=dict(color="#F43F5E", width=2, dash="dot"),
            marker=dict(size=6),
            hovertemplate="%{x}<br>Issues: %{y:,}<extra></extra>",
        )
    )

    fig_trend.update_layout(
        **PLOTLY_LAYOUT_DEFAULTS,
        height=320,
        xaxis_title="Collection Timestamp (UTC)",
        yaxis_title="Metric Count",
        hovermode="x unified",
    )
    st.plotly_chart(fig_trend, width="stretch")


# -----------------------------------------------------------------------------
# Main Application Entrypoint
# -----------------------------------------------------------------------------
def main():
    # Sidebar Controls
    with st.sidebar:
        st.markdown("### ⚡ GitPulse Control Panel")
        st.markdown("Local-First GitHub Analytics")

        db_path_input = st.text_input(
            "SQLite Database Path",
            value=str(DEFAULT_DB_PATH),
            help="Path to the SQLite database file.",
        )

        st.markdown("---")
        st.markdown("**🔄 Data Refresh**")
        if st.button("Refresh Analytics", type="primary", width="stretch"):
            refresh_pipeline(db_path_input)
            st.success("Analytics refreshed and exported to data/processed/")

        st.markdown("---")
        st.markdown("**📥 Ingest Repositories**")
        st.caption("Collect public GitHub repository metadata without an API key:")
        default_ingest_repos = "pallets/flask, psf/requests, fastapi/fastapi"
        repos_to_ingest = st.text_input(
            "Repositories (comma-separated)",
            value=default_ingest_repos,
            help="Enter public repositories in owner/repo format",
            key="sidebar_ingest_input",
        )
        if st.button("Fetch & Ingest Data", width="stretch", help="Collects metadata intentionally without requiring an API key"):
            repo_list = [r.strip() for r in repos_to_ingest.split(",") if r.strip()]
            if repo_list:
                with st.spinner("Fetching public repository metadata from GitHub API..."):
                    success, msg = trigger_ingestion(repo_list, db_path_input)
                    if success:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.warning(msg)
            else:
                st.error("Please enter at least one repository in owner/repo format.")

        st.markdown("---")
        st.markdown("**📁 Database Diagnostics**")
        db_file = Path(db_path_input)
        if db_file.exists():
            size_kb = db_file.stat().st_size / 1024
            mod_time = datetime.fromtimestamp(db_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            st.caption(f"Status: **Connected**\n\nSize: **{size_kb:.1f} KB**\n\nModified: **{mod_time}**")
        else:
            st.caption(f"Status: **Initialized (Empty)**\n\nLocation: `{db_path_input}`")

        st.markdown("---")
        st.caption("GitPulse ELT Pipeline v1.0\n\nFree & Local-First")

    # Load Analytics Data
    data = load_analytics_data(db_path_input)

    # Empty Database Safeguard
    if data["raw_repos"].empty:
        st.markdown(
            """
            <div class="dashboard-header">
                <h1 class="dashboard-title">⚡ GitPulse Analytics</h1>
                <div class="dashboard-subtitle">Local-First GitHub ELT Intelligence</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.info(
            "👋 **Welcome to GitPulse!** No repository data is currently in the SQLite database.\n\n"
            "Because runtime data and local databases are excluded from Git for clean deployment, "
            "the database starts unpopulated. You can trigger collection right away without an API key!"
        )

        st.markdown("### How to Collect Data")
        tab_cloud, tab_cli = st.tabs(["🌐 In-App Ingestion (One-Click)", "💻 PowerShell CLI (Local)"])

        with tab_cloud:
            st.markdown(
                "Click below to fetch sample public repositories (`pallets/flask`, `psf/requests`, `fastapi/fastapi`) "
                "using GitHub's unauthenticated REST API. No credentials or setup required."
            )
            if st.button("🚀 Fetch Sample Repositories Now", type="primary", key="empty_state_ingest_btn"):
                with st.spinner("Connecting to GitHub API and populating database..."):
                    success, msg = trigger_ingestion(
                        ["pallets/flask", "psf/requests", "fastapi/fastapi"],
                        db_path_input,
                    )
                    if success:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.warning(msg)

        with tab_cli:
            st.markdown(
                "If running locally on Windows, you can populate the database from PowerShell:\n\n"
                "```powershell\n"
                "python -m src.ingest --repos pallets/flask psf/requests fastapi/fastapi\n"
                "python -m src.transform\n"
                "```\n\n"
                "Then click **Refresh Analytics** in the sidebar."
            )
        return

    # Render Dashboard Sections
    render_header(data["pipeline_runs"])
    render_overview(data)
    render_repository_rankings(data)
    render_language_analytics(data)
    render_repository_activity(data)
    render_historical_trends(data)


if __name__ == "__main__":
    main()
