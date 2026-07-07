from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RUNTIME_DIR = DATA_DIR / "runtime"
GENERATED_DIR = DATA_DIR / "generated"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
EVIDENCE_DIR = DATA_DIR / "evidence"
HISTORY_CSV = RUNTIME_DIR / "query_level_history.csv"
LOCAL_TZ = ZoneInfo("America/Chicago")

sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT))
from config import X_COUNTS_COST_PER_REQUEST  # noqa: E402
from check_x_usage import get_x_usage  # noqa: E402


st.set_page_config(
    page_title="X Trend Lab",
    layout="wide",
)


def count_files(directory: Path, pattern: str) -> int:
    if not directory.exists():
        return 0
    return len(list(directory.glob(pattern)))


def latest_file(directory: Path, pattern: str) -> str:
    if not directory.exists():
        return "n/a"

    files = sorted(directory.glob(pattern))
    return files[-1].name if files else "n/a"


def snapshot_status_counts() -> dict[str, int]:
    counts = {
        "total_batches": count_files(SNAPSHOTS_DIR, "batch_*.json"),
        "total_queries": 0,
        "evaluated": 0,
        "open": 0,
    }

    if not SNAPSHOTS_DIR.exists():
        return counts

    for path in SNAPSHOTS_DIR.glob("batch_*.json"):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            counts["open"] += 1
            continue

        counts["total_queries"] += len(payload.get("queries", []))
        evaluation = payload.get("evaluation") or {}
        if evaluation.get("status") == "evaluated":
            counts["evaluated"] += 1
        else:
            counts["open"] += 1

    return counts


@st.cache_data(ttl=60)
def load_history() -> pd.DataFrame:
    if not HISTORY_CSV.exists():
        return pd.DataFrame()

    df = pd.read_csv(HISTORY_CSV)
    for col in ["saved_at", "evaluated_at", "generated_created_at"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    numeric_cols = [
        "query_index",
        "t0_3h",
        "future_3h",
        "growth_ratio",
        "realized_score",
        "future_bucket_count",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["saved_at"]).copy()
    df["evaluation_status"] = "evaluated"
    return df


@st.cache_data(ttl=60)
def load_open_queries() -> pd.DataFrame:
    rows = []
    if not SNAPSHOTS_DIR.exists():
        return pd.DataFrame()

    for path in SNAPSHOTS_DIR.glob("batch_*.json"):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue

        evaluation = payload.get("evaluation") or {}
        if evaluation.get("status") == "evaluated":
            continue

        saved_at = pd.to_datetime(payload.get("saved_at"), utc=True, errors="coerce")
        if pd.isna(saved_at):
            continue

        counts_df = pd.DataFrame(payload.get("counts_snapshot", []))
        if not counts_df.empty:
            counts_df["bucket_end"] = pd.to_datetime(counts_df["bucket_end"], utc=True, errors="coerce")
            counts_df["tweet_count"] = pd.to_numeric(counts_df["tweet_count"], errors="coerce").fillna(0)

        for query_index, query_payload in enumerate(payload.get("queries", [])):
            query = query_payload.get("query")
            if counts_df.empty or not query:
                t0_3h = 0
            else:
                t0_df = (
                    counts_df[counts_df["query"] == query]
                    .sort_values("bucket_end")
                    .tail(3)
                )
                t0_3h = int(t0_df["tweet_count"].sum()) if not t0_df.empty else 0

            rows.append(
                {
                    "batch_id": payload.get("batch_id", path.stem),
                    "saved_at": saved_at,
                    "query_index": query_index,
                    "query": query,
                    "domain": query_payload.get("domain"),
                    "mode": query_payload.get("mode"),
                    "reason": query_payload.get("reason"),
                    "t0_3h": t0_3h,
                    "future_3h": pd.NA,
                    "growth_ratio": pd.NA,
                    "realized_score": pd.NA,
                    "future_bucket_count": pd.NA,
                    "evaluation_status": "open",
                }
            )

    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def load_x_usage() -> dict:
    try:
        usage = get_x_usage(days=30)
    except Exception as exc:
        return {"error": str(exc)}

    if usage.get("estimated_spend_usd") is None and usage.get("project_usage") is not None:
        usage["estimated_spend_usd"] = round(
            int(usage["project_usage"]) * X_COUNTS_COST_PER_REQUEST,
            4,
        )
        usage["estimated_cost_per_usage_unit_usd"] = X_COUNTS_COST_PER_REQUEST

    return usage


def render_header() -> None:
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()

    st.title("X Trend Lab")
    header_cols = st.columns([3, 1])
    header_cols[0].caption("Wireframe rebuild")
    if header_cols[1].button("Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.session_state["last_data_refresh"] = now_local.strftime("%Y-%m-%d %H:%M:%S %Z")
        st.toast("Data refreshed")

    col_a, col_b = st.columns(2)
    col_a.metric("Local Time", now_local.strftime("%Y-%m-%d %H:%M:%S %Z"))
    col_b.metric("UTC Time", now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"))
    if st.session_state.get("last_data_refresh"):
        st.caption(f"Last manual refresh: {st.session_state['last_data_refresh']}")


def render_basics() -> None:
    status = snapshot_status_counts()
    x_usage = load_x_usage()

    cols = st.columns(4)
    cols[0].metric("Total Batches", f"{status['total_batches']:,}")
    cols[1].metric("Total Queries", f"{status['total_queries']:,}")
    cols[2].metric("Evaluated", f"{status['evaluated']:,}")
    cols[3].metric("Open", f"{status['open']:,}")

    artifact_cols = st.columns(4)
    artifact_cols[0].caption("Latest Generated")
    artifact_cols[0].write(latest_file(GENERATED_DIR, "generated_queries_*.json"))
    artifact_cols[1].caption("Latest Snapshot")
    artifact_cols[1].write(latest_file(SNAPSHOTS_DIR, "batch_*.json"))
    artifact_cols[2].caption("Latest Evidence Plan")
    artifact_cols[2].write(latest_file(EVIDENCE_DIR, "history_evidence_plan_*.json"))
    artifact_cols[3].caption("Query History")
    artifact_cols[3].write(HISTORY_CSV.name if HISTORY_CSV.exists() else "n/a")

    usage_cols = st.columns(4)
    usage_cols[0].caption("X Est. Spend")
    usage_cols[0].write(
        f"${x_usage['estimated_spend_usd']:,.2f}"
        if x_usage.get("estimated_spend_usd") is not None
        else "n/a"
    )
    usage_cols[1].caption("X Usage Units")
    usage_cols[1].write(
        f"{int(x_usage['project_usage']):,}"
        if x_usage.get("project_usage") is not None
        else "n/a"
    )
    usage_cols[2].caption("X Usage Cap")
    usage_cols[2].write(
        f"{int(x_usage['project_cap']):,}"
        if x_usage.get("project_cap") is not None
        else "n/a"
    )
    usage_cols[3].caption("Cap Reset Day")
    usage_cols[3].write(x_usage.get("cap_reset_day", "n/a"))

    if x_usage.get("error"):
        st.caption(f"X API usage unavailable: {x_usage['error']}")


def add_query_time_offsets(df: pd.DataFrame, spread_minutes: int) -> pd.DataFrame:
    out = df.copy()
    if "batch_id" not in out.columns or "query_index" not in out.columns:
        out["plot_time"] = out["saved_at"]
        return out

    batch_sizes = out.groupby("batch_id")["query_index"].transform("max").fillna(0) + 1
    centered_index = out["query_index"].fillna(0) - ((batch_sizes - 1) / 2)
    step_seconds = (spread_minutes * 60) / batch_sizes.clip(lower=1)
    out["plot_time"] = out["saved_at"] + pd.to_timedelta(centered_index * step_seconds, unit="s")
    return out


def render_query_plot() -> None:
    history = load_history()
    open_queries = load_open_queries()
    st.divider()
    st.subheader("Query Outcome Map")

    if history.empty and open_queries.empty:
        st.info("No query rows available yet.")
        return

    try:
        import plotly.graph_objects as go
    except Exception:
        st.info("Plotly is not installed yet. Run `pip install plotly` to enable the interactive plot.")
        return

    y_options = {
        "Realized Score": "realized_score",
        "Initial Volume": "t0_3h",
        "Future Volume": "future_3h",
        "Growth Ratio": "growth_ratio",
        "Absolute Change": "absolute_change",
    }
    controls = st.columns([1, 1, 2])
    y_label = controls[0].selectbox("Y Axis", list(y_options.keys()))
    color_by = controls[1].selectbox("Color", ["mode", "domain"])
    spread_minutes = controls[2].slider("Within-batch spread", 1, 60, 18)

    plot_df = pd.concat([history, open_queries], ignore_index=True)
    plot_df["absolute_change"] = plot_df["future_3h"].fillna(0) - plot_df["t0_3h"].fillna(0)
    plot_df = add_query_time_offsets(plot_df, spread_minutes=spread_minutes)
    plot_df["plot_time"] = plot_df["plot_time"].dt.tz_convert(LOCAL_TZ)
    plot_df["saved_at_display"] = plot_df["saved_at"].dt.tz_convert(LOCAL_TZ).dt.strftime("%Y-%m-%d %H:%M:%S %Z")

    y_col = y_options[y_label]
    plot_df["y_value"] = plot_df[y_col]
    open_mask = plot_df["evaluation_status"].eq("open")
    if y_col == "t0_3h":
        plot_df.loc[open_mask, "y_value"] = plot_df.loc[open_mask, "t0_3h"]
    else:
        plot_df.loc[open_mask, "y_value"] = 0

    log_volume_axis = y_col in {"t0_3h", "future_3h"}
    if log_volume_axis:
        plot_df["y_value"] = plot_df["y_value"].fillna(0).clip(lower=0) + 1

    plot_df = plot_df.dropna(subset=["plot_time", "y_value"])

    if plot_df.empty:
        st.info("No rows are available for the selected y-axis.")
        return

    x_min = plot_df["plot_time"].min()
    x_max = plot_df["plot_time"].max()
    x_pad = max((x_max - x_min) * 0.04, pd.Timedelta(minutes=20))

    plot_df["volume_size"] = plot_df["t0_3h"].fillna(0).clip(lower=0) + 1
    plot_df["marker_size"] = (plot_df["volume_size"].pow(0.35) * 4).clip(lower=5, upper=22)

    fig = go.Figure()
    color_col = color_by if color_by in plot_df.columns else "mode"
    evaluated_df = plot_df[~plot_df["evaluation_status"].eq("open")]
    open_df = plot_df[plot_df["evaluation_status"].eq("open")]

    for name, group in evaluated_df.groupby(color_col, dropna=False, sort=True):
        label = str(name) if pd.notna(name) else "unknown"
        fig.add_trace(
            go.Scattergl(
                x=group["plot_time"],
                y=group["y_value"],
                mode="markers",
                name=label,
                marker={
                    "size": group["marker_size"],
                    "opacity": 0.72,
                    "line": {"width": 0.5, "color": "rgba(20,20,20,0.35)"},
                },
                customdata=group[
                    [
                        "query",
                        "domain",
                        "mode",
                        "batch_id",
                        "query_index",
                        "saved_at_display",
                        "t0_3h",
                        "future_3h",
                        "growth_ratio",
                        "realized_score",
                    ]
                ].astype(str),
                hovertemplate=(
                    "<b>%{customdata[1]}</b> / %{customdata[2]}<br>"
                    "Batch: %{customdata[3]} · Query %{customdata[4]}<br>"
                    "Saved: %{customdata[5]}<br>"
                    "t0_3h: %{customdata[6]} · future_3h: %{customdata[7]}<br>"
                    "growth_ratio: %{customdata[8]} · realized_score: %{customdata[9]}<br>"
                    "<br>%{customdata[0]}"
                    "<extra></extra>"
                ),
            )
        )

    if not open_df.empty:
        fig.add_trace(
            go.Scattergl(
                x=open_df["plot_time"],
                y=open_df["y_value"],
                mode="markers",
                name="open",
                marker={
                    "size": open_df["marker_size"],
                    "color": "#16a34a",
                    "opacity": 0.82,
                    "line": {"width": 0.7, "color": "rgba(20,20,20,0.45)"},
                },
                customdata=open_df[
                    [
                        "query",
                        "domain",
                        "mode",
                        "batch_id",
                        "query_index",
                        "saved_at_display",
                        "t0_3h",
                        "future_3h",
                        "growth_ratio",
                        "realized_score",
                    ]
                ].astype(str),
                hovertemplate=(
                    "<b>OPEN</b> · %{customdata[1]} / %{customdata[2]}<br>"
                    "Batch: %{customdata[3]} · Query %{customdata[4]}<br>"
                    "Saved: %{customdata[5]}<br>"
                    "t0_3h: %{customdata[6]} · pending evaluation<br>"
                    "<br>%{customdata[0]}"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        height=620,
        margin={"l": 24, "r": 24, "t": 20, "b": 24},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        xaxis_title="Batch saved_at America/Chicago with query_index display offset",
        yaxis_title=f"{y_label} (log scale, +1)" if log_volume_axis else y_label,
    )
    if log_volume_axis:
        fig.update_yaxes(type="log", tickformat=",")

    fig.update_xaxes(
        range=[x_min - x_pad, x_max + x_pad],
        rangeslider={"visible": True, "thickness": 0.08},
        rangeselector={
            "buttons": [
                {"count": 6, "label": "6h", "step": "hour", "stepmode": "backward"},
                {"count": 12, "label": "12h", "step": "hour", "stepmode": "backward"},
                {"count": 1, "label": "1d", "step": "day", "stepmode": "backward"},
                {"step": "all", "label": "all"},
            ]
        },
    )
    fig.add_hline(y=0, line_width=1, line_dash="dot", line_color="rgba(0,0,0,0.45)")

    st.plotly_chart(fig, use_container_width=True)


def render_placeholders() -> None:
    st.divider()
    st.subheader("Sections")

    cols = st.columns(3)
    cols[0].container(border=True).write("Current momentum")
    cols[1].container(border=True).write("Historical evidence")
    cols[2].container(border=True).write("Generated queries")


def main() -> None:
    render_header()
    render_basics()
    render_query_plot()
    render_placeholders()


if __name__ == "__main__":
    main()
