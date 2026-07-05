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

LATEST_PATH = MEMORY_DIR / "reasoning_memory_latest.json"
LOOKBACK_DAYS = 30
MAX_REASONING_FILES = 300
MAX_BATCH_FILES = 200
MAX_REASONED_QUERY_EXAMPLES = 300


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
                "query_results": query_results,
                "top_query_results": sorted_results[:8],
                "bottom_query_results": sorted_results[-8:] if len(sorted_results) > 8 else sorted_results,
            }
        )

    return rows


def score_float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def build_reasoning_lookup(reasoning_logs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for log in reasoning_logs:
        generated_file = log.get("generated_file")
        if not generated_file:
            continue

        for q in log.get("queries", []):
            query = q.get("query")
            if not query:
                continue
            lookup[(generated_file, query)] = {
                "generated_at": log.get("created_at"),
                "strategy_notes": log.get("strategy_notes", ""),
                "reason": q.get("reason"),
                "domain": q.get("domain"),
                "mode": q.get("mode"),
            }

    return lookup


def outcome_tags(row: dict[str, Any]) -> list[str]:
    t0_3h = score_float(row, "t0_3h")
    future_3h = score_float(row, "future_3h")
    realized_score = score_float(row, "realized_score")

    tags = []
    if realized_score > 0:
        tags.append("positive")
    elif realized_score < 0:
        tags.append("negative")
    else:
        tags.append("zero_score")

    if future_3h == 0:
        tags.append("no_signal")
    if t0_3h > 0 and future_3h == 0:
        tags.append("burst_trap")
    if t0_3h >= 50 and future_3h == 0:
        tags.append("high_t0_collapse")
    if t0_3h <= 5 and future_3h > 0:
        tags.append("low_t0_survivor")

    return tags


def build_reasoned_query_examples(
    reasoning_logs: list[dict[str, Any]],
    evaluated_batches: list[dict[str, Any]],
    max_rows: int = MAX_REASONED_QUERY_EXAMPLES,
) -> list[dict[str, Any]]:
    reasoning_lookup = build_reasoning_lookup(reasoning_logs)
    candidates: list[dict[str, Any]] = []

    for batch in evaluated_batches:
        generated_file = batch.get("generated_file")
        for result in batch.get("query_results", []):
            query = result.get("query")
            if not generated_file or not query:
                continue

            reasoning = reasoning_lookup.get((generated_file, query), {})
            candidates.append(
                {
                    "batch_id": batch.get("batch_id"),
                    "generated_file": generated_file,
                    "evaluated_at": batch.get("evaluated_at"),
                    "query": query,
                    "domain": result.get("domain") or reasoning.get("domain"),
                    "mode": result.get("mode") or reasoning.get("mode"),
                    "generation_reason": reasoning.get("reason") or result.get("reason"),
                    "t0_3h": result.get("t0_3h"),
                    "future_3h": result.get("future_3h"),
                    "growth_ratio": result.get("growth_ratio"),
                    "realized_score": result.get("realized_score"),
                    "future_bucket_count": result.get("future_bucket_count"),
                    "outcome_tags": outcome_tags(result),
                }
            )

    selected: list[dict[str, Any]] = []
    selected_ids: set[tuple[str, str]] = set()
    per_query_counts: dict[str, int] = {}

    def add_rows(rows: list[dict[str, Any]], limit: int) -> None:
        added = 0
        for row in rows:
            if added >= limit or len(selected) >= max_rows:
                return

            row_id = (row.get("batch_id") or "", row.get("query") or "")
            query = row.get("query") or ""
            if row_id in selected_ids or per_query_counts.get(query, 0) >= 3:
                continue

            selected_ids.add(row_id)
            per_query_counts[query] = per_query_counts.get(query, 0) + 1
            selected.append(row)
            added += 1

    positives = [r for r in candidates if score_float(r, "realized_score") > 0]
    negatives = [r for r in candidates if score_float(r, "realized_score") < 0]
    burst_traps = [r for r in candidates if "burst_trap" in r.get("outcome_tags", [])]
    no_signal = [r for r in candidates if "no_signal" in r.get("outcome_tags", [])]
    low_t0_survivors = [r for r in candidates if "low_t0_survivor" in r.get("outcome_tags", [])]

    add_rows(sorted(positives, key=lambda r: score_float(r, "realized_score"), reverse=True), 80)
    add_rows(sorted(negatives, key=lambda r: score_float(r, "realized_score")), 80)
    add_rows(
        sorted(
            burst_traps,
            key=lambda r: (score_float(r, "t0_3h"), -score_float(r, "realized_score")),
            reverse=True,
        ),
        50,
    )
    add_rows(sorted(no_signal, key=lambda r: score_float(r, "t0_3h"), reverse=True), 40)
    add_rows(sorted(low_t0_survivors, key=lambda r: score_float(r, "future_3h"), reverse=True), 30)
    add_rows(sorted(candidates, key=lambda r: str(r.get("evaluated_at") or ""), reverse=True), max_rows)

    return selected[:max_rows]


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
    reasoned_query_examples = build_reasoned_query_examples(reasoning_logs, evaluated_batches)
    evaluated_batch_summaries = [
        {
            "batch_id": batch.get("batch_id"),
            "generated_file": batch.get("generated_file"),
            "saved_at": batch.get("saved_at"),
            "evaluated_at": batch.get("evaluated_at"),
            "avg_realized_score": batch.get("avg_realized_score"),
            "best_query": batch.get("best_query"),
            "worst_query": batch.get("worst_query"),
        }
        for batch in evaluated_batches
    ]

    evidence = {
        "source_window": {
            "reasoning_logs_inspected": len(reasoning_logs),
            "evaluated_batches_used": len(evaluated_batches),
            "reasoned_query_examples_used": len(reasoned_query_examples),
            "days_covered": LOOKBACK_DAYS,
        },
        "reasoned_query_examples": reasoned_query_examples,
        "recent_strategy_notes": build_strategy_note_examples(reasoning_logs, max_rows=12),
        "evaluated_batches": evaluated_batch_summaries,
    }
    return evidence


def response_schema() -> dict:
    memory_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mechanism": {"type": "string", "maxLength": 100},
            "rule": {"type": "string", "maxLength": 220},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["mechanism", "rule", "confidence"],
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "updated_at": {"type": "string"},
            "source_window": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "reasoning_logs_inspected": {"type": "integer"},
                    "evaluated_batches_used": {"type": "integer"},
                    "reasoned_query_examples_used": {"type": "integer"},
                    "days_covered": {"type": "integer"},
                },
                "required": [
                    "reasoning_logs_inspected",
                    "evaluated_batches_used",
                    "reasoned_query_examples_used",
                    "days_covered",
                ],
            },
            "prompt_summary": {
                "type": "string",
                "maxLength": 900,
                "description": "Compact prompt-ready memory summary. No long evidence prose.",
            },
            "exploit_guidance": {
                "type": "array",
                "maxItems": 5,
                "items": memory_item,
            },
            "explore_guidance": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "mechanism": {"type": "string", "maxLength": 100},
                        "query_shape": {"type": "string", "maxLength": 180},
                        "reason": {"type": "string", "maxLength": 180},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["mechanism", "query_shape", "reason", "confidence"],
                },
            },
            "avoid_guidance": {
                "type": "array",
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "failure_mode": {"type": "string", "maxLength": 100},
                        "reason": {"type": "string", "maxLength": 180},
                    },
                    "required": ["failure_mode", "reason"],
                },
            },
            "decay_rules": {
                "type": "array",
                "maxItems": 5,
                "items": {"type": "string", "maxLength": 180},
            },
            "query_shape_rules": {
                "type": "array",
                "maxItems": 6,
                "items": {"type": "string", "maxLength": 180},
            },
        },
        "required": [
            "updated_at",
            "source_window",
            "prompt_summary",
            "exploit_guidance",
            "explore_guidance",
            "avoid_guidance",
            "decay_rules",
            "query_shape_rules",
        ],
    }


def distill_reasoning_memory(evidence: dict[str, Any]) -> dict[str, Any]:
    client = OpenAI()

    system_prompt = (
        "You are distilling compact reasoning memory for an autonomous trend-query generator. "
        "Your job is to produce terse, prompt-ready general guidance from recent local evidence. "
        "Do not change or reinterpret the fitness function itself. The fitness function is fixed elsewhere. "
        "Prefer short rules over narrative evidence. Do not write a report. "
        "Prefer robust patterns supported by repeated evidence over one-off flashy examples. "
        "Each rule must consider both what scored well and what scored poorly. "
        "Reward reasoning patterns that led to positive realized scores and punish reasoning patterns that led to negative, no-signal, or burst-trap outcomes. "
        "Treat high short-term burstiness with caution when volume is tiny. "
        "Repetition alone does not imply success. "
        "Use evaluated outcomes when available to separate repeated themes from actually productive themes. "
        "Express lessons as reusable query mechanisms, not domain recaps. "
        "Avoid naming specific domains unless the lesson cannot be expressed generally. "
        "Make every field usable inside a future generation prompt. Return only JSON matching the schema."
    )

    user_prompt = (
        "Here is recent evidence from the local trend-query system. "
        "The fitness function is fixed and must not be altered. "
        "Distill only durable, general lessons into a compact policy. "
        "Compare positive examples against negative/no-signal/burst-trap examples before writing each rule. "
        "Do not overfit to a single transient event; describe reusable mechanisms and failure modes. "
        "Prefer abstract patterns such as scheduled live-event status, legal/procedural action, operational outage, high-volume decay, "
        "over-constrained recency tails, or low-volume survivor rather than specific topic names. "
        "Use no more than 5 exploit guidance items, 8 explore guidance items, 10 avoid items, "
        "5 decay rules, and 6 query-shape rules. Keep wording concise.\n\n"
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
                "name": "reasoning_memory",
                "schema": response_schema(),
            }
        },
    )

    payload = json.loads(response.output_text)
    return payload


def save_memory_snapshot(memory: dict[str, Any]) -> tuple[Path, Path]:
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    memory["updated_at"] = now.isoformat()
    archive_path = ARCHIVE_DIR / f"reasoning_memory_{timestamp}.json"

    serialized = json.dumps(memory, indent=2, default=str)
    archive_path.write_text(serialized)
    LATEST_PATH.write_text(serialized)

    return archive_path, LATEST_PATH


def main() -> None:
    evidence = build_evidence_packet()
    memory = distill_reasoning_memory(evidence)

    archive_path, latest_path = save_memory_snapshot(memory)

    print(f"Saved latest reasoning memory: {latest_path}")
    print(f"Saved archived reasoning memory: {archive_path}")
    print(f"Reasoning logs inspected: {evidence['source_window']['reasoning_logs_inspected']}")
    print(f"Reasoned query examples used: {evidence['source_window']['reasoned_query_examples_used']}")
    print(f"Evaluated batches used: {evidence['source_window']['evaluated_batches_used']}")


if __name__ == "__main__":
    main()
