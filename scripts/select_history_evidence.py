import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MODEL
from scripts.history_evidence import (
    EVIDENCE_PLAN_LATEST,
    EVIDENCE_SELECTED_LATEST,
    HISTORY_CSV,
    summarize_query_history_schema,
    select_history_evidence,
)


def main() -> None:
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

    selected, plan_payload = select_history_evidence(max_records=max_records)
    plan = plan_payload.get("plan", plan_payload)

    if selected.empty and plan_payload.get("error"):
        print(plan_payload["error"])
        sys.exit(1)

    print("Selection strategy:")
    print(plan.get("strategy", plan_payload.get("strategy", "")))
    print()
    print(f"Selected rows:     {len(selected)}")
    print(f"Wrote latest plan: {EVIDENCE_PLAN_LATEST}")
    print(f"Wrote latest CSV:  {EVIDENCE_SELECTED_LATEST}")
    print()

    if not selected.empty:
        print("Selected evidence preview:")
        preview = selected.head(20)
        if isinstance(preview, pd.DataFrame):
            print(preview.to_string(index=False))

    print()
    print("Plan payload:")
    print(json.dumps(plan_payload, indent=2, default=str))


if __name__ == "__main__":
    main()
