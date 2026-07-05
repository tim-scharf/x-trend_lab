import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

sys.path.append(str(Path(__file__).resolve().parents[1]))

from config import (
    DB_PATH,
    GENERATED_DIR,
    REASONING_DIR,
    MODEL,
    NUM_EXPLOIT,
    NUM_EXPLORE,
    NUM_QUERIES,
    MAX_ATTEMPTS,
)


load_dotenv()

ROOT = Path(__file__).resolve().parents[1]




REASONING_DIR.mkdir(parents=True, exist_ok=True)

RUNTIME_DIR = ROOT / "data" / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

EVIDENCE_DIR = ROOT / "data" / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_CSV = RUNTIME_DIR / "query_level_history.csv"

EVIDENCE_PLAN_LATEST = EVIDENCE_DIR / "history_evidence_plan_latest.json"
EVIDENCE_SELECTED_LATEST = EVIDENCE_DIR / "history_evidence_selected_latest.csv"


DEFAULT_EVIDENCE_COLUMNS = [
    "batch_id",
    "saved_at",
    "evaluated_at",
    "generated_file",
    "model_used",
    "query_index",
    "query",
    "domain",
    "mode",
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


def load_score_summary() -> pd.DataFrame:
    """
    Build a current short-term momentum summary from the X counts DB.

    This is NOT delayed realized fitness. It only summarizes current bucket momentum:
    recent_3h vs previous_3h, with a simple low-volume penalty.
    """
    fallback = pd.DataFrame(
        [
            {
                "query": '"OpenAI" OR "Grok"',
                "hourly_buckets": 0,
                "total_7d": 0,
                "recent_3h": 0,
                "recent_6h": 0,
                "prev_3h": 0,
                "velocity": 0.0,
                "acceleration": 0.0,
                "score": 0.0,
            }
        ]
    )

    if not DB_PATH.exists():
        return fallback

    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT query, bucket_start, bucket_end, tweet_count
        FROM counts
        ORDER BY query, bucket_start
        """,
        con,
    )
    con.close()

    if df.empty:
        return fallback

    df["bucket_start"] = pd.to_datetime(df["bucket_start"], utc=True)
    df["bucket_end"] = pd.to_datetime(df["bucket_end"], utc=True)

    rows = []
    for query, group in df.groupby("query", sort=False):
        group = group.sort_values("bucket_start").reset_index(drop=True)
        values = group["tweet_count"].astype(int).tolist()

        hourly_buckets = len(values)
        total_7d = int(sum(values))
        recent_3h = int(sum(values[-3:])) if hourly_buckets >= 3 else int(sum(values))
        prev_3h = int(sum(values[-6:-3])) if hourly_buckets >= 6 else 0
        recent_6h = int(sum(values[-6:])) if hourly_buckets >= 6 else recent_3h

        velocity = recent_3h / (prev_3h + 1)
        acceleration = (recent_3h - prev_3h) / (prev_3h + 1)
        score = (0.7 * velocity) + (0.3 * acceleration)

        min_volume = 50
        if recent_3h < min_volume:
            score *= 0.2

        rows.append(
            {
                "query": query,
                "hourly_buckets": hourly_buckets,
                "total_7d": total_7d,
                "recent_3h": recent_3h,
                "recent_6h": recent_6h,
                "prev_3h": prev_3h,
                "velocity": round(float(velocity), 4),
                "acceleration": round(float(acceleration), 4),
                "score": round(float(score), 4),
            }
        )

    score_df = pd.DataFrame(rows).sort_values(
        ["score", "recent_3h", "total_7d"], ascending=False
    ).reset_index(drop=True)

    return score_df


def summarize_query_history_schema(history_path: Path) -> dict:
    """
    Read query_level_history.csv and return compact shape / dtype / missingness /
    range info for an LLM selector.
    """
    if not history_path.exists():
        return {
            "exists": False,
            "path": str(history_path),
            "error": "query_level_history.csv not found",
        }

    df = pd.read_csv(history_path)

    summary = {
        "exists": True,
        "path": str(history_path),
        "shape": {
            "rows": int(df.shape[0]),
            "columns": int(df.shape[1]),
        },
        "columns": [],
    }

    for col in df.columns:
        s = df[col]
        col_info = {
            "name": col,
            "dtype": str(s.dtype),
            "non_null": int(s.notna().sum()),
            "nulls": int(s.isna().sum()),
            "null_pct": round(float(s.isna().mean()), 4),
        }

        numeric_s = pd.to_numeric(s, errors="coerce")
        numeric_non_null = numeric_s.notna().sum()
        original_non_null = s.notna().sum()

        if original_non_null > 0 and numeric_non_null >= max(3, int(0.7 * original_non_null)):
            col_info.update(
                {
                    "kind": "numeric",
                    "min": float(numeric_s.min()),
                    "max": float(numeric_s.max()),
                    "mean": float(numeric_s.mean()),
                }
            )
        else:
            values = s.dropna().astype(str)
            col_info.update(
                {
                    "kind": "categorical_or_text",
                    "unique_count": int(values.nunique()),
                    "sample_values": values.drop_duplicates().head(8).tolist(),
                }
            )

        summary["columns"].append(col_info)

    return summary


def query_history_selection_schema() -> dict:
    """
    JSON schema for a safe selection plan.
    The model returns intent, not executable Python.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strategy": {
                "type": "string",
                "description": "Brief explanation of which historical records should be selected and why.",
            },
            "filters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "future_nonzero": {"type": ["boolean", "null"]},
                    "score_positive": {"type": ["boolean", "null"]},
                    "score_negative": {"type": ["boolean", "null"]},
                    "burst_trap": {"type": ["boolean", "null"]},
                    "no_signal": {"type": ["boolean", "null"]},
                    "mode": {"type": ["string", "null"]},
                    "domain_contains": {"type": ["string", "null"]},
                    "min_t0_3h": {"type": ["number", "null"]},
                    "max_t0_3h": {"type": ["number", "null"]},
                    "min_future_3h": {"type": ["number", "null"]},
                    "min_realized_score": {"type": ["number", "null"]},
                    "max_realized_score": {"type": ["number", "null"]},
                },
                "required": [
                    "future_nonzero",
                    "score_positive",
                    "score_negative",
                    "burst_trap",
                    "no_signal",
                    "mode",
                    "domain_contains",
                    "min_t0_3h",
                    "max_t0_3h",
                    "min_future_3h",
                    "min_realized_score",
                    "max_realized_score",
                ],
            },
            "sort_by": {
                "type": "string",
                "description": "Existing column to sort by.",
            },
            "ascending": {
                "type": "boolean",
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Existing columns to include in the selected evidence output.",
            },
            "max_records": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": [
            "strategy",
            "filters",
            "sort_by",
            "ascending",
            "columns",
            "max_records",
        ],
    }


def coerce_bool_series(s: pd.Series) -> pd.Series:
    """
    Robust bool parsing for CSV columns that may be bools or strings.
    """
    if s.dtype == bool:
        return s

    return s.astype(str).str.lower().isin(["true", "1", "yes"])


def apply_history_selection_plan(df: pd.DataFrame, plan: dict, max_records: int = 100) -> pd.DataFrame:
    """
    Apply an LLM-chosen plan safely with whitelisted operations.
    """
    out = df.copy()
    filters = plan.get("filters", {}) or {}

    bool_filters = [
        "future_nonzero",
        "score_positive",
        "score_negative",
        "burst_trap",
        "no_signal",
    ]

    for col in bool_filters:
        val = filters.get(col)
        if val is not None and col in out.columns:
            out = out[coerce_bool_series(out[col]) == bool(val)]

    mode = filters.get("mode")
    if mode and "mode" in out.columns:
        out = out[out["mode"].fillna("").astype(str).str.lower() == str(mode).lower()]

    domain_contains = filters.get("domain_contains")
    if domain_contains and "domain" in out.columns:
        out = out[
            out["domain"]
            .fillna("")
            .astype(str)
            .str.contains(str(domain_contains), case=False, na=False)
        ]

    numeric_filters = [
        ("min_t0_3h", "t0_3h", ">="),
        ("max_t0_3h", "t0_3h", "<="),
        ("min_future_3h", "future_3h", ">="),
        ("min_realized_score", "realized_score", ">="),
        ("max_realized_score", "realized_score", "<="),
    ]

    for filter_name, col, op in numeric_filters:
        val = filters.get(filter_name)
        if val is None or col not in out.columns:
            continue

        numeric = pd.to_numeric(out[col], errors="coerce")

        if op == ">=":
            out = out[numeric >= float(val)]
        elif op == "<=":
            out = out[numeric <= float(val)]

    sort_by = plan.get("sort_by")
    ascending = bool(plan.get("ascending", False))

    if sort_by in out.columns:
        sort_numeric = pd.to_numeric(out[sort_by], errors="coerce")
        if sort_numeric.notna().sum() > 0:
            out = out.assign(_sort_value=sort_numeric).sort_values(
                "_sort_value", ascending=ascending, na_position="last"
            ).drop(columns=["_sort_value"])
        else:
            out = out.sort_values(sort_by, ascending=ascending, na_position="last")

    requested_cols = plan.get("columns") or DEFAULT_EVIDENCE_COLUMNS
    keep_cols = [c for c in requested_cols if c in out.columns]

    if not keep_cols:
        keep_cols = [c for c in DEFAULT_EVIDENCE_COLUMNS if c in out.columns]

    out = out[keep_cols]

    limit = min(int(plan.get("max_records", max_records)), max_records, 100)
    return out.head(limit).reset_index(drop=True)


def ask_openai_for_history_selection(schema_summary: dict, max_records: int = 100) -> dict:
    """
    Ask OpenAI which historical records would help the next generator.
    """
    client = OpenAI()

    prompt = f"""
You are choosing historical evidence for an adaptive X/Twitter query-generation loop.

The generator already sees current short-term momentum from the counts DB.
Your job is to select records from query_level_history.csv that teach delayed realized fitness.

Useful evidence includes:
- query forms that survived into future_3h
- query forms that became burst traps
- domains or modes that repeatedly failed
- low t0_3h records with later future_3h signal
- high t0_3h records that collapsed
- diverse examples, not near-duplicates

Return a safe JSON selection plan only.
Do not return Python code.
Do not invent columns.
Do not request more than {max_records} records.

Available CSV schema/profile:
{json.dumps(schema_summary, indent=2)}
"""

    response = client.responses.create(
        model=MODEL,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "query_history_selection",
                "schema": query_history_selection_schema(),
            }
        },
    )

    plan = json.loads(response.output_text)
    plan["max_records"] = min(int(plan.get("max_records", max_records)), max_records, 100)
    return plan


def select_history_evidence(max_records: int = 100) -> tuple[pd.DataFrame, dict]:
    """
    Select historical delayed-fitness evidence for the generator prompt.

    If query history is unavailable or selection fails, return an empty frame and
    a plan explaining the failure. This keeps generation resilient.
    """
    schema_summary = summarize_query_history_schema(HISTORY_CSV)

    if not schema_summary.get("exists"):
        return pd.DataFrame(), {
            "strategy": "No query_level_history.csv available.",
            "error": schema_summary.get("error"),
            "max_records": max_records,
        }

    try:
        plan = ask_openai_for_history_selection(schema_summary, max_records=max_records)
        df = pd.read_csv(HISTORY_CSV)
        selected = apply_history_selection_plan(df, plan, max_records=max_records)
    except Exception as e:
        return pd.DataFrame(), {
            "strategy": "History selection failed; continuing without historical evidence.",
            "error": str(e),
            "max_records": max_records,
        }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    plan_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_used": MODEL,
        "history_csv": str(HISTORY_CSV),
        "schema_shape": schema_summary.get("shape"),
        "plan": plan,
        "selected_rows": int(len(selected)),
    }

    plan_path = EVIDENCE_DIR / f"history_evidence_plan_{timestamp}.json"
    csv_path = EVIDENCE_DIR / f"history_evidence_selected_{timestamp}.csv"

    plan_path.write_text(json.dumps(plan_payload, indent=2))
    EVIDENCE_PLAN_LATEST.write_text(json.dumps(plan_payload, indent=2))

    selected.to_csv(csv_path, index=False)
    selected.to_csv(EVIDENCE_SELECTED_LATEST, index=False)

    return selected, plan_payload


def build_history_evidence_block(history_df: pd.DataFrame, plan_payload: dict) -> str:
    """
    Format the selected evidence into the generator prompt.
    """
    if history_df.empty:
        return f"""
No historical delayed-fitness records selected.

Selection note:
{plan_payload.get("strategy") or plan_payload.get("error", "")}
"""

    plan = plan_payload.get("plan", plan_payload)

    return f"""
Historical delayed-fitness evidence selected from query_level_history.csv:

Selection strategy:
{plan.get("strategy", "")}

Interpretation:
- future_3h is delayed observed volume after the original snapshot.
- realized_score is delayed fitness.
- burst_trap means t0_3h was positive but future_3h collapsed to 0.
- future_nonzero means the query had some delayed continuation.
- Use this evidence to preserve working query DNA and avoid repeated failure forms.
- Do not simply copy all winners. Mutate the useful structure.

Selected historical records:
{history_df.to_string(index=False)}
"""


def build_prompt(score_df: pd.DataFrame, history_block: str = "") -> str:
    prompt_df = score_df[
        ["query", "hourly_buckets", "total_7d", "recent_3h", "prev_3h", "velocity", "score"]
    ].head(80)

    table = prompt_df.to_string(index=False)

    return f"""
You are helping evolve an X/Twitter topic-count sampling strategy.

Goal:
Find early trend candidates using X recent-counts queries, not individual posts.

Current query performance:
{table}

Historical delayed-fitness evidence:
{history_block}

Interpretation rules:
- Queries with huge volume may be too broad and noisy.
- Queries with near-zero volume are too narrow.
- Good queries should have enough volume to measure, but not be so broad that they are always noisy.
- Prefer X search syntax that works with the counts endpoint.
- Use quoted phrases and OR groups.
- Prefer specific entity-event combinations over generic evergreen topics.
- During exploration, it is okay to try narrower or more experimental phrasing.
- Avoid pulling individual tweets.
- Avoid malformed search strings. Do not over-escape quotes.
- Avoid private, credential-related, unsafe, adult, or spammy terms.

Scoring hints:
- recent_3h = newest 3 hourly buckets
- prev_3h = the 3 buckets before that
- velocity compares recent_3h against prev_3h
- score is a short-term momentum score, not just raw volume
- huge evergreen topics may still be too broad even with strong score
- prefer measurable but not permanently noisy queries

Historical evidence hints:
- Delayed realized fitness matters more than current momentum.
- Preserve query structures that repeatedly produced future_nonzero or positive realized_score.
- Avoid repeating burst traps unless you intentionally mutate the event/time coupling.
- Prefer entity + event verb + time/status qualifier when the evidence supports it.
- If historical evidence is clumpy, keep the useful structure but explore adjacent entities/domains.

Domain labeling:
- For each query, choose a concise domain label.
- Reuse an existing domain label if it clearly fits.
- If no existing label fits, create a new lowercase snake_case domain.
- The domain is metadata only; the query text is what matters most.
- Good examples: ai, consumer_tech, geopolitics, gaming_outages, sports_injuries, entertainment, finance, crypto, macro, legal.

Exploration rule:
Generate exactly {NUM_QUERIES} queries:
- Exactly {NUM_EXPLOIT} queries must be mode "exploit": refine or mutate existing promising clusters.
- Exactly {NUM_EXPLORE} queries must be mode "explore": test adjacent or new domains.

Prefer a mix of:
- event queries
- rumor / leak / injury / lawsuit / release phrasing
- more specific mutations of currently strong clusters
- at least a few historically informed mutations
- at least a few exploratory candidates not directly copied from evidence

Return only data matching the JSON schema. No markdown. No code fences.
"""


def response_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strategy_notes": {"type": "string"},
            "queries": {
                "type": "array",
                "minItems": NUM_QUERIES,
                "maxItems": NUM_QUERIES,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string"},
                        "domain": {
                            "type": "string",
                            "description": (
                                "Concise topic/domain label. Reuse an existing label if it fits; "
                                "otherwise create a new lowercase snake_case label."
                            ),
                        },
                        "mode": {"type": "string", "enum": ["exploit", "explore"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["query", "domain", "mode", "reason"],
                },
            },
        },
        "required": ["strategy_notes", "queries"],
    }


def validate_payload(payload: dict) -> None:
    queries = payload.get("queries", [])
    if len(queries) != NUM_QUERIES:
        raise ValueError(f"Expected {NUM_QUERIES} queries, got {len(queries)}")

    exploit = sum(q["mode"] == "exploit" for q in queries)
    explore = sum(q["mode"] == "explore" for q in queries)
    if exploit != NUM_EXPLOIT or explore != NUM_EXPLORE:
        raise ValueError(
            f"Expected {NUM_EXPLOIT} exploit / {NUM_EXPLORE} explore, got {exploit}/{explore}"
        )

    for q in queries:
        domain = q.get("domain", "").strip()
        if not domain:
            raise ValueError("Empty domain found in generated payload")
        if len(domain) > 50:
            raise ValueError(f"Domain label too long: {domain}")
        if not re.match(r"^[A-Za-z0-9_ -]+$", domain):
            raise ValueError(f"Suspicious domain label: {domain}")

    deduped = {q["query"].strip() for q in queries}
    if len(deduped) != len(queries):
        raise ValueError("Duplicate queries found in generated payload")


def generate_queries(prompt: str) -> dict:
    client = OpenAI()
    last_err = None

    for _ in range(MAX_ATTEMPTS):
        try:
            response = client.responses.create(
                model=MODEL,
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "query_generation",
                        "schema": response_schema(),
                    }
                },
            )
            payload = json.loads(response.output_text)
            validate_payload(payload)
            return payload
        except Exception as e:
            last_err = e

    raise RuntimeError(
        f"Failed to generate valid query payload after {MAX_ATTEMPTS} attempts: {last_err}"
    )


def save_reasoning_file(
    payload: dict,
    prompt: str,
    score_df: pd.DataFrame,
    generated_filename: str,
    evidence_df: pd.DataFrame,
    evidence_plan_payload: dict,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    reason_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_used": MODEL,
        "generated_file": generated_filename,
        "strategy_notes": payload.get("strategy_notes", ""),
        "query_count": len(payload.get("queries", [])),
        "queries": payload.get("queries", []),
        "seed_ideas_used": [],
        "score_summary_rows": score_df.to_dict(orient="records"),
        "history_evidence_plan": evidence_plan_payload,
        "history_evidence_rows": evidence_df.to_dict(orient="records"),
        "history_evidence_row_count": int(len(evidence_df)),
        "prompt": prompt,
    }

    path = REASONING_DIR / f"reasoning_{timestamp}.json"
    with path.open("w") as f:
        json.dump(reason_payload, f, indent=2, default=str)
    return path


def save_query_generation(payload: dict, evidence_plan_payload: dict, evidence_df: pd.DataFrame) -> Path:
    created_at = datetime.now(timezone.utc).isoformat()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    payload["model_used"] = MODEL
    payload["created_at"] = created_at
    payload["generation_settings"] = {
        "num_queries": NUM_QUERIES,
        "num_exploit": NUM_EXPLOIT,
        "num_explore": NUM_EXPLORE,
        "history_evidence_enabled": True,
        "history_evidence_rows": int(len(evidence_df)),
    }

    payload["history_evidence"] = {
        "selected_rows": int(len(evidence_df)),
        "plan_strategy": (
            evidence_plan_payload.get("plan", {}).get("strategy")
            if isinstance(evidence_plan_payload.get("plan"), dict)
            else evidence_plan_payload.get("strategy")
        ),
        "plan_file": str(EVIDENCE_PLAN_LATEST),
        "selected_file": str(EVIDENCE_SELECTED_LATEST),
    }

    path = GENERATED_DIR / f"generated_queries_{timestamp}.json"
    with path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return path


def main() -> None:
    max_evidence_records = 100
    if len(sys.argv) >= 2:
        max_evidence_records = min(int(sys.argv[1]), 100)

    score_df = load_score_summary()

    evidence_df, evidence_plan_payload = select_history_evidence(
        max_records=max_evidence_records
    )
    history_block = build_history_evidence_block(evidence_df, evidence_plan_payload)

    prompt = build_prompt(score_df, history_block=history_block)
    payload = generate_queries(prompt)

    generated_path = save_query_generation(payload, evidence_plan_payload, evidence_df)
    reasoning_path = save_reasoning_file(
        payload=payload,
        prompt=prompt,
        score_df=score_df,
        generated_filename=generated_path.name,
        evidence_df=evidence_df,
        evidence_plan_payload=evidence_plan_payload,
    )

    print(f"History evidence rows selected: {len(evidence_df)}")
    print(f"History evidence latest CSV: {EVIDENCE_SELECTED_LATEST}")
    print(f"History evidence latest plan: {EVIDENCE_PLAN_LATEST}")
    print(f"Saved generated queries to: {generated_path}")
    print(f"Saved reasoning log to: {reasoning_path}")
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
