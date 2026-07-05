import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from scripts.query_summary import build_query_summary


DEBUG_TOP_N = 25


def main() -> None:
    scores = build_query_summary()

    if scores.empty:
        print("No valid scores yet.")
        return

    print("\nCandidate scores:")
    print(scores.to_string(index=False))

    print("\nDebug top rows:")
    debug_cols = [
        "query",
        "hourly_buckets",
        "recent_3h",
        "prev_3h",
        "recent_6h",
        "velocity",
        "acceleration",
        "simple_score",
        "candidate_score",
    ]
    print(scores[debug_cols].head(DEBUG_TOP_N).to_string(index=False))


if __name__ == "__main__":
    main()