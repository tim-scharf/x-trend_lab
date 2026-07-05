import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from config import MODEL

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
REASONING_DIR = ROOT / "data" / "reasoning"
SNAPSHOTS_DIR = ROOT / "data" / "snapshots"
MEMORY_DIR = ROOT / "data" / "memory"
ARCHIVE_DIR = MEMORY_DIR / "archive"

MEMORY_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

LATEST_PATH = MEMORY_DIR / "strategy_memory_latest.json"
LOOKBACK_DAYS = 30
MAX_REASONING_FILES = 300
MAX_BATCH_FILES = 200
MAX_SCORE_ROWS = 60


def parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def load_recent_reasoning_logs(days: int = LOOKBACK_DAYS) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows: list[dict[str, Any]] = []

    for path in sorted(REASONING_DIR.glob("reasoning_*.json"))[-MAX_REASONING_FILES:]:
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue

        created_at = parse_iso(payload.get("created_at", ""))
        if created_at and created_at < cutoff:
            continue

        rows.append(
            {
                "created_at": payload.get("created_at"),
                "generated_file": payload.get("generated_file"),
                "strategy_notes": payload.get("strategy_notes", ""),
                "query_count": payload.get("query_count", 0),
                "queries": [
                    {
                        "query": q.get("query"),
                        "mode": q.get("mode"),
                        "domain": q.get("domain"),
                        "reason": q.get("reason"),
                    }
                    for q in payload.get("queries", [])
                ],
                "score_summary_rows": payload.get("score_summary_rows", [])[:20],
            }
        )

    return rows


def load_recent_evaluated_batches(days: int = LOOKBACK_DAYS) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows: list[dict[str, Any]] = []

    for path in sorted(SNAPSHOTS_DIR.glob("batch_*.json"))[-MAX_BATCH_FILES:]:
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue

        evaluated_at = parse_iso(payload.get("evaluated_at", ""))
        if not evaluated_at:
            continue
        if evaluated_at < cutoff:
            continue

        evaluation = payload.get("evaluation", {})
        query_results = evaluation.get("query_results", [])

        sorted_results = sorted(
            query_results,
            key=lambda x: x.get("realized_score", 0.0),
            reverse=True,
        )

        rows.append(
            {
                "batch_id": payload.get("batch_id"),
                "generated_file": payload.get("generated_file"),
                "saved_at": payload.get("saved_at"),
                "evaluated_at": payload.get("evaluated_at"),
                "avg_realized_score": evaluation.get("avg_realized_score"),
                "best_query": evaluation.get("best_query"),
                "worst_query": evaluation.get("worst_query"),
                "top_query_results": sorted_results[:8],
                "bottom_query_results": sorted_results[-8:] if len(sorted_results) > 8 else sorted_results,
            }
        )

    return rows


def build_score_examples(reasoning_logs: list[dict[str, Any]], max_rows: int = MAX_SCORE_ROWS) -> list[dict[str, Any]]:
    seen = set()
    rows: list[dict[str, Any]] = []

    for log in reasoning_logs:
        for row in log.get("score_summary_rows", []):
            query = row.get("query")
            if not query or query in seen:
                continue

            seen.add(query)
            rows.append(
                {
                    "query": query,
                    "recent_3h": row.get("recent_3h"),
                    "prev_3h": row.get("prev_3h"),
                    "recent_6h": row.get("recent_6h"),
                    "simple_score": row.get("simple_score"),
                    "candidate_score": row.get("candidate_score"),
                    "normalized_candidate_score": row.get("normalized_candidate_score"),
                    "normalized_simple_score": row.get("normalized_simple_score"),
                }
            )

            if len(rows) >= max_rows:
                return rows

    return rows


def build_query_examples(reasoning_logs: list[dict[str, Any]], max_rows: int = 120) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for log in reasoning_logs:
        for q in log.get("queries", []):
            rows.append(
                {
                    "query": q.get("query"),
                    "mode": q.get("mode"),
                    "domain": q.get("domain"),
                }
            )
            if len(rows) >= max_rows:
                return rows

    return rows


def build_strategy_note_examples(reasoning_logs: list[dict[str, Any]], max_rows: int = 25) -> list[str]:
    notes = []
    for log in reasoning_logs:
        note = (log.get("strategy_notes") or "").strip()
        if note:
            notes.append(note)
        if len(notes) >= max_rows:
            break
    return notes


def build_evidence_packet() -> dict[str, Any]:
    reasoning_logs = load_recent_reasoning_logs()
    evaluated_batches = load_recent_evaluated_batches()

    evidence = {
        "source_window": {
            "reasoning_logs_used": len(reasoning_logs),
            "evaluated_batches_used": len(evaluated_batches),
            "days_covered": LOOKBACK_DAYS,
        },
        "recent_strategy_notes": build_strategy_note_examples(reasoning_logs),
        "recent_queries": build_query_examples(reasoning_logs),
        "recent_score_examples": build_score_examples(reasoning_logs),
        "evaluated_batches": evaluated_batches,
    }
    return evidence


def response_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "updated_at": {"type": "string"},
            "source_window": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "reasoning_logs_used": {"type": "integer"},
                    "evaluated_batches_used": {"type": "integer"},
                    "days_covered": {"type": "integer"},
                },
                "required": [
                    "reasoning_logs_used",
                    "evaluated_batches_used",
                    "days_covered",
                ],
            },
            "dominant_archetypes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "pattern": {"type": "string"},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["pattern", "evidence", "confidence"],
                },
            },
            "failing_archetypes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "pattern": {"type": "string"},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["pattern", "evidence", "confidence"],
                },
            },
            "overused_themes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "underexplored_promising_themes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "score_disagreement_lessons": {
                "type": "array",
                "items": {"type": "string"},
            },
            "diversity_state": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["narrowing", "moderate", "broad", "uncertain"]},
                    "note": {"type": "string"},
                },
                "required": ["status", "note"],
            },
            "prompt_policy": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "exploit_bias": {"type": "string"},
                    "explore_bias": {"type": "string"},
                    "avoid": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["exploit_bias", "explore_bias", "avoid"],
            },
        },
        "required": [
            "updated_at",
            "source_window",
            "dominant_archetypes",
            "failing_archetypes",
            "overused_themes",
            "underexplored_promising_themes",
            "score_disagreement_lessons",
            "diversity_state",
            "prompt_policy",
        ],
    }


def distill_strategy_memory(evidence: dict[str, Any]) -> dict[str, Any]:
    client = OpenAI()

    system_prompt = (
        "You are distilling strategic memory for an autonomous trend-query generator. "
        "Your job is to infer policy-level lessons from recent local evidence. "
        "Do not change or reinterpret the fitness function itself. The fitness function is fixed elsewhere. "
        "Infer dominant query archetypes, failing or fragile archetypes, overused themes, "
        "underexplored but promising themes, lessons from disagreements between short-term burstiness and robustness, "
        "and a concise prompt policy for future generation. "
        "Prefer robust patterns supported by repeated evidence over one-off flashy examples. "
        "Treat high short-term burstiness with caution when volume is tiny. "
        "Repetition alone does not imply success. "
        "Use evaluated outcomes when available to separate repeated themes from actually productive themes. "
        "Be honest about uncertainty. Return only JSON matching the schema."
    )

    user_prompt = (
        "Here is recent evidence from the local trend-query system. "
        "The fitness function is fixed and must not be altered. "
        "You are only distilling learned search-policy memory.\n\n"
        f"Evidence JSON:\n{json.dumps(evidence, indent=2, default=str)}"
    )

    response = client.responses.create(
        model=MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "strategy_memory",
                "schema": response_schema(),
            }
        },
    )

    payload = json.loads(response.output_text)
    return payload


def save_memory_snapshot(memory: dict[str, Any]) -> tuple[Path, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_path = ARCHIVE_DIR / f"strategy_memory_{timestamp}.json"

    serialized = json.dumps(memory, indent=2, default=str)
    archive_path.write_text(serialized)
    LATEST_PATH.write_text(serialized)

    return archive_path, LATEST_PATH


def main() -> None:
    evidence = build_evidence_packet()
    memory = distill_strategy_memory(evidence)

    archive_path, latest_path = save_memory_snapshot(memory)

    print(f"Saved latest strategy memory: {latest_path}")
    print(f"Saved archived strategy memory: {archive_path}")
    print(f"Reasoning logs used: {evidence['source_window']['reasoning_logs_used']}")
    print(f"Evaluated batches used: {evidence['source_window']['evaluated_batches_used']}")


if __name__ == "__main__":
    main()
