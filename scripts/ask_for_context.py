import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

load_dotenv()

from config import MODEL, EVAL_LAG_HOURS
from scripts.query_summary import build_query_summary

DATA_DIR = ROOT / "data"
REASONING_DIR = DATA_DIR / "reasoning"
MEMORY_DIR = DATA_DIR / "memory"
RUNTIME_DIR = DATA_DIR / "runtime"
CONTEXT_REQUEST_DIR = DATA_DIR / "context_requests"
CONTEXT_REQUEST_DIR.mkdir(parents=True, exist_ok=True)

MAX_SCORE_ROWS = 50
MAX_RECENT_REASONING = 8
MAX_HISTORY_EXAMPLES_PER_BUCKET = 12


def latest_files(directory: Path, pattern: str, limit: int):
    return sorted(directory.glob(pattern), reverse=True)[:limit]


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def json_safe(obj):
    return json.loads(json.dumps(obj, default=str))


def load_strategy_memory():
    path = MEMORY_DIR / "strategy_memory_latest.json"
    if not path.exists():
        return None
    return load_json(path)


def load_recent_reasoning(limit: int = MAX_RECENT_REASONING):
    rows = []
    for path in latest_files(REASONING_DIR, "reasoning_*.json", limit):
        payload = load_json(path)
        if not payload:
            continue
        generated_queries = payload.get("generated_queries") or payload.get("queries") or []
        rows.append(
            {
                "file": path.name,
                "created_at": payload.get("created_at"),
                "model_used": payload.get("model_used"),
                "strategy_notes": payload.get("strategy_notes"),
                "seed_ideas_used": payload.get("seed_ideas_used"),
                "history_evidence_row_count": payload.get("history_evidence_row_count"),
                "history_evidence_plan": payload.get("history_evidence_plan"),
                "generated_queries": generated_queries[:20],
            }
        )
    return rows


def load_score_summary():
    scores = build_query_summary()
    if scores.empty:
        return []
    cols = [
        "query",
        "hourly_buckets",
        "total_7d",
        "recent_3h",
        "prev_3h",
        "recent_6h",
        "velocity",
        "acceleration",
        "simple_score",
        "candidate_score",
    ]
    existing_cols = [c for c in cols if c in scores.columns]
    return json_safe(scores[existing_cols].head(MAX_SCORE_ROWS).to_dict(orient="records"))


def coerce_bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.lower().isin(["true", "1", "yes"])


def compact_rows(df: pd.DataFrame, max_rows: int = MAX_HISTORY_EXAMPLES_PER_BUCKET):
    cols = [
        "batch_id",
        "saved_at",
        "evaluated_at",
        "model_used",
        "domain",
        "mode",
        "query",
        "reason",
        "t0_3h",
        "future_3h",
        "growth_ratio",
        "realized_score",
        "future_bucket_count",
        "future_nonzero",
        "score_positive",
        "score_negative",
        "score_zero",
        "burst_trap",
        "no_signal",
    ]
    keep = [c for c in cols if c in df.columns]
    return json_safe(df[keep].head(max_rows).to_dict(orient="records"))


def load_query_history_summary() -> dict:
    path = RUNTIME_DIR / "query_level_history.csv"
    if not path.exists():
        return {"exists": False, "path": str(path), "error": "query_level_history.csv not found"}
    try:
        df = pd.read_csv(path)
    except Exception as e:
        return {"exists": False, "path": str(path), "error": f"Could not read query_level_history.csv: {e}"}
    if df.empty:
        return {"exists": True, "path": str(path), "rows": 0, "message": "query_level_history.csv is empty"}

    for col in ["t0_3h", "future_3h", "growth_ratio", "realized_score", "future_bucket_count", "query_index"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["future_nonzero", "score_positive", "score_negative", "score_zero", "burst_trap", "no_signal"]:
        if col in df.columns:
            df[col] = coerce_bool_series(df[col])

    summary = {
        "exists": True,
        "path": str(path),
        "shape": {"rows": int(df.shape[0]), "columns": int(df.shape[1])},
        "distinct_batches": int(df["batch_id"].nunique()) if "batch_id" in df.columns else None,
        "distinct_queries": int(df["query"].nunique()) if "query" in df.columns else None,
    }

    if "realized_score" in df.columns:
        rs = df["realized_score"]
        summary["score_summary"] = {
            "avg_realized_score": float(rs.mean()),
            "median_realized_score": float(rs.median()),
            "pct_positive": float((rs > 0).mean()),
            "pct_zero": float((rs == 0).mean()),
            "pct_negative": float((rs < 0).mean()),
        }
    if "future_3h" in df.columns:
        summary.setdefault("score_summary", {})["pct_future_nonzero"] = float((df["future_3h"].fillna(0) > 0).mean())
    if "burst_trap" in df.columns:
        summary.setdefault("score_summary", {})["pct_burst_trap"] = float(df["burst_trap"].mean())

    if "domain" in df.columns:
        domain_rows = []
        for domain, g in df.groupby("domain", dropna=False):
            row = {"domain": str(domain), "n": int(len(g))}
            if "realized_score" in g.columns:
                row["avg_realized_score"] = float(g["realized_score"].mean())
                row["pct_positive"] = float((g["realized_score"] > 0).mean())
            if "future_3h" in g.columns:
                row["pct_future_nonzero"] = float((g["future_3h"].fillna(0) > 0).mean())
            if "burst_trap" in g.columns:
                row["pct_burst_trap"] = float(g["burst_trap"].mean())
            domain_rows.append(row)
        summary["domain_summary"] = sorted(
            domain_rows,
            key=lambda r: (r.get("pct_positive", 0), r.get("pct_future_nonzero", 0), r.get("n", 0)),
            reverse=True,
        )[:20]

    if "mode" in df.columns:
        mode_rows = []
        for mode, g in df.groupby("mode", dropna=False):
            row = {"mode": str(mode), "n": int(len(g))}
            if "realized_score" in g.columns:
                row["avg_realized_score"] = float(g["realized_score"].mean())
                row["pct_positive"] = float((g["realized_score"] > 0).mean())
            if "future_3h" in g.columns:
                row["pct_future_nonzero"] = float((g["future_3h"].fillna(0) > 0).mean())
            if "burst_trap" in g.columns:
                row["pct_burst_trap"] = float(g["burst_trap"].mean())
            mode_rows.append(row)
        summary["mode_summary"] = mode_rows

    examples = {}
    if "realized_score" in df.columns:
        examples["top_positive_survivors"] = compact_rows(
            df[df["realized_score"] > 0].sort_values(["realized_score", "future_3h"], ascending=False, na_position="last")
        )
        examples["worst_negative_queries"] = compact_rows(
            df[df["realized_score"] < 0].sort_values(["realized_score", "t0_3h"], ascending=[True, False], na_position="last")
        )
    if "burst_trap" in df.columns:
        examples["largest_burst_traps"] = compact_rows(
            df[df["burst_trap"]].sort_values(["t0_3h", "realized_score"], ascending=[False, True], na_position="last")
        )
    if {"t0_3h", "future_3h"}.issubset(df.columns):
        examples["low_t0_future_nonzero"] = compact_rows(
            df[(df["t0_3h"].fillna(0) <= 10) & (df["future_3h"].fillna(0) > 0)].sort_values(
                ["future_3h", "realized_score"], ascending=False, na_position="last"
            )
        )
        examples["zero_signal_dead_queries"] = compact_rows(
            df[(df["t0_3h"].fillna(0) == 0) & (df["future_3h"].fillna(0) == 0)].sort_values(
                ["saved_at", "query"], ascending=False, na_position="last"
            )
        )
    summary["examples"] = examples
    return json_safe(summary)


def build_prompt(score_summary, strategy_memory, recent_reasoning, query_history_summary):
    return f'''
You are helping operate a local trend-detection lab.

The system generates X/Twitter recent-count queries, collects hourly count windows,
saves prediction batches, and later evaluates whether those queries improved over
a forward evaluation window.

Current fitness target:
- Improve delayed forward performance over approximately {EVAL_LAG_HOURS} hours.
- Good queries should not merely have current volume.
- Good queries should have near-term continuation, acceleration, or follow-on burst potential.

Your task:
Ask for missing information that would help the next query-generation step produce better X/Twitter count queries.

IMPORTANT CONSTRAINT:
Every requested piece of information must be realistic, obtainable, and operationally executable by a simple agent or script.

Do NOT ask for vague things like:
- "find trends"
- "search the internet"
- "know what people are thinking"
- "predict what will go viral"
- "analyze all tweets"
- "get private data"
- "use paid data we probably do not have"

Good requests are bounded and executable, such as:
- "Find major sports games/events in the next 24 hours for NFL/NBA/WNBA/UFC."
- "Find companies with earnings or guidance events in the next 48 hours."
- "Find scheduled macroeconomic releases or Fed events in the next 72 hours."
- "Check current top stories from a small set of public news/RSS/search sources for AI regulation."
- "For the top 5 high-scoring query clusters, suggest related entities or canonical event verbs."
- "Identify generated queries with repeated zero counts and explain likely stale/missing event anchors."
- "Given burst-trap query examples, find the missing specific event anchors that could make them less generic."

Each request must include:
- request_type: short machine-readable label
- question: the exact thing the context agent should retrieve
- why_it_helps_fitness: why this could improve the {EVAL_LAG_HOURS}h score
- expected_output_shape: what the answer should look like
- suggested_method: how a simple agent could obtain it
- max_calls_or_cost: realistic bound, e.g. "1 web search", "3 RSS sources", "local DB only"
- priority: high / medium / low

Return JSON only.

Use this exact schema:

{{
  "created_at": "ISO timestamp",
  "eval_lag_hours": {EVAL_LAG_HOURS},
  "overall_assessment": "brief assessment of what information is missing",
  "context_requests": [
    {{
      "request_type": "string",
      "question": "string",
      "why_it_helps_fitness": "string",
      "expected_output_shape": "string",
      "suggested_method": "string",
      "max_calls_or_cost": "string",
      "priority": "high|medium|low"
    }}
  ],
  "do_not_request": [
    "string"
  ]
}}

Limit context_requests to 5.
Prefer high-value, bounded requests over broad curiosity.
Use the flattened query-level delayed-fitness history heavily: it shows what actually lived, died, or became a burst trap.

Current score summary:
{json.dumps(score_summary, indent=2, default=str)}

Flattened query-level delayed-fitness history:
{json.dumps(query_history_summary, indent=2, default=str)}

Current strategy memory:
{json.dumps(strategy_memory, indent=2, default=str)}

Recent reasoning logs:
{json.dumps(recent_reasoning, indent=2, default=str)}
'''.strip()


def extract_text_response(response):
    if hasattr(response, "output_text") and response.output_text:
        return response.output_text
    chunks = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def call_llm(prompt: str):
    client = OpenAI()
    return client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "You are a cautious research-planning agent. "
                    "You only request information that is realistic, obtainable, bounded, "
                    "and useful for improving a measurable trend-query fitness function. "
                    "Return valid JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )


def main():
    score_summary = load_score_summary()
    strategy_memory = load_strategy_memory()
    recent_reasoning = load_recent_reasoning()
    query_history_summary = load_query_history_summary()

    prompt = build_prompt(
        score_summary=score_summary,
        strategy_memory=strategy_memory,
        recent_reasoning=recent_reasoning,
        query_history_summary=query_history_summary,
    )

    response = call_llm(prompt)
    raw_text = extract_text_response(response)

    try:
        payload = json.loads(raw_text)
    except Exception:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "eval_lag_hours": EVAL_LAG_HOURS,
            "parse_error": True,
            "raw_response": raw_text,
        }

    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    payload.setdefault("eval_lag_hours", EVAL_LAG_HOURS)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = CONTEXT_REQUEST_DIR / f"context_request_{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str))

    print(f"Saved context request: {out_path}")
    print()
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
