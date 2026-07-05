import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

sys.path.append(str(Path(__file__).resolve().parents[1]))

from config import MODEL


ROOT = Path(__file__).resolve().parents[1]
HISTORY_CSV = ROOT / "data" / "runtime" / "query_level_history.csv"
RUNTIME_DIR = ROOT / "data" / "runtime"
OUT_DIR = ROOT / "data" / "evidence"
OUT_DIR.mkdir(parents=True, exist_ok=True)


DEFAULT_COLUMNS = [
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

        if numeric_non_null > 0 and numeric_non_null >= max(3, int(0.7 * s.notna().sum())):
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

    requested_cols = plan.get("columns") or DEFAULT_COLUMNS
    keep_cols = [c for c in requested_cols if c in out.columns]

    if not keep_cols:
        keep_cols = [c for c in DEFAULT_COLUMNS if c in out.columns]

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


def main() -> None:
    load_dotenv()

    max_records = 100
    if len(sys.argv) >= 2:
        max_records = min(int(sys.argv[1]), 100)

    schema_summary = summarize_query_history_schema(HISTORY_CSV)
    if not schema_summary.get("exists"):
        print(schema_summary.get("error", "History file not found."))
        sys.exit(1)

    print("=== Query History Evidence Picker ===")
    print(f"History CSV:       {HISTORY_CSV}")
    print(f"Rows:              {schema_summary['shape']['rows']}")
    print(f"Columns:           {schema_summary['shape']['columns']}")
    print(f"Max records:       {max_records}")
    print(f"Model:             {MODEL}")
    print()

    plan = ask_openai_for_history_selection(schema_summary, max_records=max_records)

    df = pd.read_csv(HISTORY_CSV)
    selected = apply_history_selection_plan(df, plan, max_records=max_records)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    plan_path = OUT_DIR / f"history_evidence_plan_{timestamp}.json"
    csv_path = OUT_DIR / f"history_evidence_selected_{timestamp}.csv"
    latest_plan_path = OUT_DIR / "history_evidence_plan_latest.json"
    latest_csv_path = OUT_DIR / "history_evidence_selected_latest.csv"

    plan_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_used": MODEL,
        "history_csv": str(HISTORY_CSV),
        "schema_shape": schema_summary.get("shape"),
        "plan": plan,
        "selected_rows": int(len(selected)),
    }

    plan_path.write_text(json.dumps(plan_payload, indent=2))
    latest_plan_path.write_text(json.dumps(plan_payload, indent=2))

    selected.to_csv(csv_path, index=False)
    selected.to_csv(latest_csv_path, index=False)

    print("Selection strategy:")
    print(plan.get("strategy", ""))
    print()
    print(f"Selected rows:     {len(selected)}")
    print(f"Wrote plan:        {plan_path}")
    print(f"Wrote CSV:         {csv_path}")
    print(f"Wrote latest plan: {latest_plan_path}")
    print(f"Wrote latest CSV:  {latest_csv_path}")
    print()

    if not selected.empty:
        print("Selected evidence preview:")
        print(selected.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
