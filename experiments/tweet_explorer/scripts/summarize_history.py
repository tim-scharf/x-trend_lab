from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import SUMMARY_MODEL
from helper import read_json, summarize_probe, write_json
from planner import compact_history_for_planner, probe_from_history


def summary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "tweet_id",
            "audience",
            "tweet_summary",
            "investigation_strategy",
            "what_happened",
            "notable_findings",
            "dead_ends_or_limits",
            "plain_english_summary",
            "memory_for_next_llm",
        ],
        "properties": {
            "tweet_id": {"type": "string"},
            "audience": {"type": "string", "enum": ["human", "llm"]},
            "tweet_summary": {"type": "string"},
            "investigation_strategy": {"type": "string"},
            "what_happened": {"type": "string"},
            "notable_findings": {"type": "array", "items": {"type": "string"}},
            "dead_ends_or_limits": {"type": "array", "items": {"type": "string"}},
            "plain_english_summary": {"type": "string"},
            "memory_for_next_llm": {"type": "string"},
        },
    }


def summarize_history(
    history: dict[str, Any],
    *,
    audience: str = "llm",
    model: str = SUMMARY_MODEL,
) -> dict[str, Any]:
    if audience not in {"human", "llm"}:
        raise ValueError("audience must be 'human' or 'llm'.")

    from openai import OpenAI

    prompt = build_summary_prompt(history, audience=audience)
    response = OpenAI().responses.create(
        model=model,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "tweet_explorer_history_summary",
                "schema": summary_schema(),
            }
        },
    )
    return {
        "model": model,
        "audience": audience,
        "prompt": prompt,
        "summary": json.loads(response.output_text),
        "usage": usage_to_dict(getattr(response, "usage", None)),
    }


def build_summary_prompt(history: dict[str, Any], *, audience: str) -> list[dict[str, str]]:
    probe = probe_from_history(history)
    compact = compact_history_for_planner(history)
    budget = history.get("budget") or {}
    system_prompt = (
        "You write plain-English case notes for an X API tweet investigation. "
        "Do not perform cost accounting, analytics, scoring, or formal evidence citation. "
        "Do not invent exact numbers, step IDs, or prices. "
        "Use careful King's English: clear, direct, and readable. "
        "Return only JSON matching the schema."
    )
    user_prompt = f"""
Audience: {audience}

Summarize what happened in this run for a human investigator and for later LLM memory.
Keep it plain-English and qualitative.

Write:
- what the original tweet was about
- what strategy the investigator used
- what happened during the run
- the main qualitative findings
- obvious dead ends or limits
- a compact memory paragraph the next LLM can use

Do not include:
- supporting step IDs
- exact cost estimates
- invented confidence scores
- formal analytics that Python should calculate

Run state:
{json.dumps({"state": history.get("state"), "budget": budget}, indent=2, default=str)}

Probe summary:
{json.dumps(summarize_probe(probe), indent=2, default=str)}

Compact run history:
{json.dumps(compact, indent=2, default=str)}
"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def append_history_summary(
    history_path: Path,
    *,
    audience: str = "llm",
    model: str = SUMMARY_MODEL,
) -> dict[str, Any]:
    history = read_json(history_path)
    record = summarize_history(history, audience=audience, model=model)
    now = datetime.now(timezone.utc).isoformat()
    sequence = int(history.get("next_sequence") or len(history.get("steps", [])))
    step = {
        "sequence": sequence,
        "kind": "history_summary",
        "created_at": now,
        "status": "complete",
        "model": record["model"],
        "audience": record["audience"],
        "summary": record["summary"],
        "usage": record["usage"],
    }
    history.setdefault("steps", []).append(step)
    history["next_sequence"] = sequence + 1
    history["updated_at"] = now
    write_json(history_path, history)
    return step


def usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return json.loads(json.dumps(usage, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize a tweet_explorer history file.")
    parser.add_argument("history_json", type=Path, help="Path to tweet_explorer history.json.")
    parser.add_argument(
        "--audience",
        choices=["human", "llm"],
        default="llm",
        help="Summary target. Default: llm.",
    )
    parser.add_argument("--model", default=SUMMARY_MODEL, help=f"Summary model. Default: {SUMMARY_MODEL}.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append the summary as a history_summary step in history.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.append:
        step = append_history_summary(
            args.history_json,
            audience=args.audience,
            model=args.model,
        )
        print(json.dumps(step["summary"], indent=2, default=str))
        return

    history = read_json(args.history_json)
    record = summarize_history(history, audience=args.audience, model=args.model)
    print(json.dumps(record["summary"], indent=2, default=str))


if __name__ == "__main__":
    main()
