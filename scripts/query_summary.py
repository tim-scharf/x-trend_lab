import math
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from config import DB_PATH


SIMPLE_MIN_VOLUME_3H = 50
CANDIDATE_MIN_VOLUME_3H = 100


def load_counts() -> pd.DataFrame:
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT query, bucket_start, bucket_end, tweet_count FROM counts",
        con,
    )
    con.close()

    if df.empty:
        return df

    df["bucket_start"] = pd.to_datetime(df["bucket_start"], utc=True)
    df["bucket_end"] = pd.to_datetime(df["bucket_end"], utc=True)
    return df


def compute_growth_score(baseline: float, current: float) -> float:
    if baseline <= 0:
        return 0.0

    growth = current / baseline
    if growth <= 1:
        return 0.0

    return math.log(growth)


def summarize_query(group: pd.DataFrame) -> pd.Series | None:
    group = group.sort_values("bucket_start").reset_index(drop=True)

    if len(group) < 6:
        return None

    values = group["tweet_count"].astype(float)

    recent_3h = float(values.tail(3).sum())
    prev_3h = float(values.iloc[-6:-3].sum())
    recent_6h = recent_3h + prev_3h

    baseline = values.iloc[:-6]
    if len(baseline) < 6:
        return None

    baseline_avg_3h = baseline.rolling(3).sum().dropna().mean()
    baseline_avg_6h = baseline.rolling(6).sum().dropna().mean()

    if pd.isna(baseline_avg_3h) or pd.isna(baseline_avg_6h):
        return None

    score_3h = compute_growth_score(float(baseline_avg_3h), recent_3h)
    score_6h = compute_growth_score(float(baseline_avg_6h), recent_6h)

    velocity = recent_3h / (prev_3h + 1.0)
    acceleration = (recent_3h - prev_3h) / (prev_3h + 1.0)

    simple_score = (0.7 * velocity) + (0.3 * acceleration)
    if recent_3h < SIMPLE_MIN_VOLUME_3H:
        simple_score *= 0.2

    volume_weight = math.log1p(recent_6h)
    candidate_score = (
        (score_3h * 0.5)
        + (score_6h * 0.2)
        + (velocity * 0.2)
        + (acceleration * 0.1)
    ) * volume_weight

    if recent_3h < CANDIDATE_MIN_VOLUME_3H:
        candidate_score *= (recent_3h / CANDIDATE_MIN_VOLUME_3H)

    return pd.Series(
        {
            "latest_bucket": group["bucket_end"].max(),
            "hourly_buckets": int(len(group)),
            "total_7d": int(values.sum()),
            "recent_3h": int(recent_3h),
            "prev_3h": int(prev_3h),
            "recent_6h": int(recent_6h),
            "baseline_avg_3h": round(float(baseline_avg_3h), 2),
            "baseline_avg_6h": round(float(baseline_avg_6h), 2),
            "score_3h": round(float(score_3h), 4),
            "score_6h": round(float(score_6h), 4),
            "velocity": round(float(velocity), 4),
            "acceleration": round(float(acceleration), 4),
            "simple_score": round(float(simple_score), 4),
            "candidate_score": round(float(candidate_score), 4),
        }
    )


def build_query_summary() -> pd.DataFrame:
    df = load_counts()

    if df.empty:
        return pd.DataFrame()

    summary = (
        df.groupby("query", group_keys=False)
        .apply(summarize_query)
        .dropna()
        .reset_index()
    )

    if summary.empty:
        return summary

    max_candidate = summary["candidate_score"].max()
    if max_candidate > 0:
        summary["normalized_candidate_score"] = (
            summary["candidate_score"] / max_candidate
        ).round(4)
    else:
        summary["normalized_candidate_score"] = 0.0

    max_simple = summary["simple_score"].max()
    if max_simple > 0:
        summary["normalized_simple_score"] = (
            summary["simple_score"] / max_simple
        ).round(4)
    else:
        summary["normalized_simple_score"] = 0.0

    summary = summary.sort_values(
        ["candidate_score", "recent_3h", "total_7d"],
        ascending=False,
    ).reset_index(drop=True)

    return summary
