from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "scripts"))

from config import DB_PATH, EVAL_LAG_HOURS, GENERATED_DIR, SNAPSHOTS_DIR  # noqa: E402
from query_summary import build_query_summary  # noqa: E402


RUNTIME_DIR = ROOT / "data" / "runtime"
REASONING_DIR = ROOT / "data" / "reasoning"
HISTORY_CSV = RUNTIME_DIR / "query_level_history.csv"


st.set_page_config(
    page_title="X Trend Lab",
    page_icon="",
    layout="wide",
)


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


@st.cache_data(ttl=60)
def load_counts() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()

    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT query, bucket_start, bucket_end, tweet_count, pulled_at
        FROM counts
        ORDER BY bucket_end
        """,
        con,
    )
    con.close()

    if df.empty:
        return df

    df["bucket_start"] = pd.to_datetime(df["bucket_start"], utc=True, errors="coerce")
    df["bucket_end"] = pd.to_datetime(df["bucket_end"], utc=True, errors="coerce")
    df["pulled_at"] = pd.to_datetime(df["pulled_at"], utc=True, errors="coerce")
    df["tweet_count"] = pd.to_numeric(df["tweet_count"], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60)
def load_query_metadata() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()

    con = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM query_metadata", con)
    except Exception:
        df = pd.DataFrame()
    con.close()
    return df


@st.cache_data(ttl=60)
def load_scores() -> pd.DataFrame:
    try:
        return build_query_summary()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_history() -> pd.DataFrame:
    if not HISTORY_CSV.exists():
        return pd.DataFrame()

    df = pd.read_csv(HISTORY_CSV)
    for col in ["saved_at", "evaluated_at", "generated_created_at"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    for col in ["t0_3h", "future_3h", "growth_ratio", "realized_score", "future_bucket_count"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=60)
def load_snapshot_index() -> pd.DataFrame:
    rows = []
    for path in sorted(SNAPSHOTS_DIR.glob("batch_*.json")):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            rows.append({"file": path.name, "status": "unreadable"})
            continue

        evaluation = payload.get("evaluation") or {}
        query_results = evaluation.get("query_results") or []
        rows.append(
            {
                "file": path.name,
                "batch_id": payload.get("batch_id", path.stem),
                "generated_file": payload.get("generated_file"),
                "model_used": payload.get("model_used"),
                "saved_at": parse_dt(payload.get("saved_at")),
                "evaluated_at": parse_dt(payload.get("evaluated_at")),
                "status": evaluation.get("status") or ("evaluated" if payload.get("evaluated_at") else "pending"),
                "query_count": len(payload.get("queries", [])),
                "snapshot_rows": len(payload.get("counts_snapshot", [])),
                "avg_realized_score": evaluation.get("avg_realized_score"),
                "best_query": (evaluation.get("best_query") or {}).get("query")
                if isinstance(evaluation.get("best_query"), dict)
                else None,
                "query_results": len(query_results),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df["saved_at"] = pd.to_datetime(df["saved_at"], utc=True, errors="coerce")
        df["evaluated_at"] = pd.to_datetime(df["evaluated_at"], utc=True, errors="coerce")
        df["avg_realized_score"] = pd.to_numeric(df["avg_realized_score"], errors="coerce")
    return df


@st.cache_data(ttl=60)
def load_latest_generated() -> dict:
    files = sorted(GENERATED_DIR.glob("generated_queries_*.json"))
    if not files:
        return {}
    path = files[-1]
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {"file": path.name, "error": "Could not parse JSON"}
    payload["_file"] = path.name
    return payload


def latest_file(directory: Path, pattern: str) -> Path | None:
    files = sorted(directory.glob(pattern))
    return files[-1] if files else None


def metric_row(counts: pd.DataFrame, history: pd.DataFrame, snapshots: pd.DataFrame) -> None:
    latest_bucket = counts["bucket_end"].max() if not counts.empty and "bucket_end" in counts else None
    evaluated = int((snapshots["status"] == "evaluated").sum()) if not snapshots.empty else 0

    cols = st.columns(5)
    cols[0].metric("Count Rows", f"{len(counts):,}")
    cols[1].metric("Tracked Queries", f"{counts['query'].nunique():,}" if not counts.empty else "0")
    cols[2].metric("Snapshots", f"{len(snapshots):,}")
    cols[3].metric("Evaluated", f"{evaluated:,}")
    cols[4].metric("Latest Bucket", latest_bucket.strftime("%m-%d %H:%M UTC") if latest_bucket is not None else "n/a")


def render_overview(counts: pd.DataFrame, history: pd.DataFrame, snapshots: pd.DataFrame, metadata: pd.DataFrame) -> None:
    metric_row(counts, history, snapshots)

    left, right = st.columns([1.2, 1])

    with left:
        st.subheader("Hourly Collection Pull Volume")
        if counts.empty:
            st.info("No count rows found yet.")
        else:
            hourly_source = counts.dropna(subset=["pulled_at"]).copy()
            hourly_source["pulled_hour"] = hourly_source["pulled_at"].dt.floor("h")
            hourly = (
                hourly_source.groupby("pulled_hour", as_index=False)
                .agg(
                    total_tweet_count=("tweet_count", "sum"),
                    records=("query", "count"),
                )
                .sort_values("pulled_hour")
            )
            st.scatter_chart(
                hourly,
                x="pulled_hour",
                y="total_tweet_count",
                size="records",
                height=260,
                use_container_width=True,
            )
            st.caption("Each point is one pulled_at hour, with tweet_count summed across count records.")

    with right:
        st.subheader("Artifacts")
        artifact_rows = [
            {"artifact": "Latest generated", "file": latest_file(GENERATED_DIR, "generated_queries_*.json")},
            {"artifact": "Latest snapshot", "file": latest_file(SNAPSHOTS_DIR, "batch_*.json")},
            {"artifact": "Latest reasoning", "file": latest_file(REASONING_DIR, "reasoning_*.json")},
            {"artifact": "Query history", "file": HISTORY_CSV if HISTORY_CSV.exists() else None},
        ]
        artifact_df = pd.DataFrame(
            {
                "artifact": row["artifact"],
                "file": row["file"].name if row["file"] else "n/a",
            }
            for row in artifact_rows
        )
        st.dataframe(artifact_df, hide_index=True, use_container_width=True)

        if not metadata.empty and "domain" in metadata.columns:
            domain_counts = metadata["domain"].fillna("unknown").value_counts().head(12)
            st.subheader("Top Metadata Domains")
            st.bar_chart(domain_counts, height=220)


def render_momentum(counts: pd.DataFrame, scores: pd.DataFrame) -> None:
    st.subheader("Current Candidate Scores")
    if scores.empty:
        st.info("No score summary is available yet. Queries need enough count buckets to score.")
        return

    top_n = st.slider("Top queries", 5, min(50, len(scores)), min(20, len(scores)))
    display_cols = [
        "query",
        "hourly_buckets",
        "total_7d",
        "recent_3h",
        "prev_3h",
        "recent_6h",
        "velocity",
        "acceleration",
        "candidate_score",
    ]
    st.dataframe(scores[[c for c in display_cols if c in scores.columns]].head(top_n), use_container_width=True)

    chart_df = scores.head(top_n).set_index("query")
    if "candidate_score" in chart_df.columns:
        st.bar_chart(chart_df["candidate_score"], height=320)

    st.subheader("Selected Query Time Series")
    if counts.empty:
        st.info("No counts available.")
        return

    default_queries = scores["query"].head(min(5, len(scores))).tolist()
    selected = st.multiselect(
        "Queries",
        scores["query"].tolist(),
        default=default_queries,
    )
    if selected:
        series = counts[counts["query"].isin(selected)].pivot_table(
            index="bucket_end",
            columns="query",
            values="tweet_count",
            aggfunc="sum",
        )
        st.line_chart(series, height=360)


def render_history(history: pd.DataFrame, snapshots: pd.DataFrame) -> None:
    st.subheader("Evaluation History")
    if history.empty:
        st.info("No query-level history found. Run `scripts/build_query_history.py` after batches are evaluated.")
        return

    cols = st.columns(4)
    cols[0].metric("History Rows", f"{len(history):,}")
    cols[1].metric("Distinct Batches", f"{history['batch_id'].nunique():,}" if "batch_id" in history else "n/a")
    cols[2].metric("Avg Realized Score", f"{history['realized_score'].mean():.3f}" if "realized_score" in history else "n/a")
    cols[3].metric("Future Nonzero", f"{(history['future_3h'].fillna(0) > 0).mean():.1%}" if "future_3h" in history else "n/a")

    left, right = st.columns(2)

    with left:
        if {"domain", "realized_score"}.issubset(history.columns):
            domain_score = (
                history.groupby("domain", dropna=False)["realized_score"]
                .mean()
                .sort_values(ascending=False)
                .head(15)
            )
            st.subheader("Average Score by Domain")
            st.bar_chart(domain_score, height=300)

    with right:
        if {"mode", "realized_score"}.issubset(history.columns):
            mode_score = history.groupby("mode", dropna=False)["realized_score"].mean()
            st.subheader("Average Score by Mode")
            st.bar_chart(mode_score, height=300)

    if not snapshots.empty and "avg_realized_score" in snapshots.columns:
        scored = snapshots.dropna(subset=["avg_realized_score"]).sort_values("saved_at")
        if not scored.empty:
            st.subheader("Batch Score Timeline")
            timeline = scored.set_index("saved_at")[["avg_realized_score"]]
            st.line_chart(timeline, height=300)

    st.subheader("Best and Worst Query Outcomes")
    sort_col = "realized_score"
    cols_to_show = [
        "saved_at",
        "domain",
        "mode",
        "t0_3h",
        "future_3h",
        "growth_ratio",
        "realized_score",
        "query",
    ]
    cols_to_show = [c for c in cols_to_show if c in history.columns]
    top = history.sort_values(sort_col, ascending=False).head(15)
    bottom = history.sort_values(sort_col, ascending=True).head(15)
    a, b = st.columns(2)
    a.dataframe(top[cols_to_show], use_container_width=True, hide_index=True)
    b.dataframe(bottom[cols_to_show], use_container_width=True, hide_index=True)


def render_snapshots(snapshots: pd.DataFrame) -> None:
    st.subheader("Snapshot Explorer")
    if snapshots.empty:
        st.info("No snapshots found.")
        return

    status_filter = st.multiselect(
        "Status",
        sorted(snapshots["status"].dropna().unique().tolist()),
        default=sorted(snapshots["status"].dropna().unique().tolist()),
    )
    filtered = snapshots[snapshots["status"].isin(status_filter)] if status_filter else snapshots
    st.dataframe(filtered.sort_values("saved_at", ascending=False), use_container_width=True, hide_index=True)

    selected_file = st.selectbox("Open snapshot", filtered["file"].tolist())
    path = SNAPSHOTS_DIR / selected_file
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        st.error(f"Could not read snapshot: {exc}")
        return

    queries = pd.DataFrame(payload.get("queries", []))
    counts = pd.DataFrame(payload.get("counts_snapshot", []))
    eval_rows = pd.DataFrame((payload.get("evaluation") or {}).get("query_results", []))

    q_col, c_col, e_col = st.columns(3)
    q_col.metric("Queries", len(queries))
    c_col.metric("Snapshot Rows", len(counts))
    e_col.metric("Evaluation Rows", len(eval_rows))

    with st.expander("Queries", expanded=True):
        st.dataframe(queries, use_container_width=True, hide_index=True)
    with st.expander("Counts Snapshot"):
        st.dataframe(counts, use_container_width=True, hide_index=True)
    if not eval_rows.empty:
        with st.expander("Evaluation Results", expanded=True):
            st.dataframe(eval_rows, use_container_width=True, hide_index=True)


def render_generated() -> None:
    st.subheader("Latest Generated Query Batch")
    payload = load_latest_generated()
    if not payload:
        st.info("No generated query files found.")
        return

    st.caption(payload.get("_file", "unknown file"))
    cols = st.columns(4)
    queries = pd.DataFrame(payload.get("queries", []))
    cols[0].metric("Queries", len(queries))
    cols[1].metric("Model", payload.get("model_used", "n/a"))
    cols[2].metric("Created", str(payload.get("created_at", "n/a"))[:19])
    cols[3].metric("Evidence Rows", payload.get("history_evidence", {}).get("selected_rows", "n/a"))

    if payload.get("strategy_notes"):
        st.write(payload["strategy_notes"])

    if not queries.empty:
        if "mode" in queries.columns:
            st.subheader("Mode Mix")
            st.bar_chart(queries["mode"].value_counts(), height=180)
        st.dataframe(queries, use_container_width=True, hide_index=True)


def main() -> None:
    st.title("X Trend Lab")
    st.caption(f"Local dashboard. Evaluation lag: {EVAL_LAG_HOURS} hours.")

    counts = load_counts()
    metadata = load_query_metadata()
    scores = load_scores()
    history = load_history()
    snapshots = load_snapshot_index()

    tabs = st.tabs([
        "Overview",
        "Current Momentum",
        "Evaluation History",
        "Snapshots",
        "Generated Queries",
    ])

    with tabs[0]:
        render_overview(counts, history, snapshots, metadata)
    with tabs[1]:
        render_momentum(counts, scores)
    with tabs[2]:
        render_history(history, snapshots)
    with tabs[3]:
        render_snapshots(snapshots)
    with tabs[4]:
        render_generated()


if __name__ == "__main__":
    main()
